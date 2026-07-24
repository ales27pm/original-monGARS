"""Typed asynchronous adapter for Ollama's native HTTP API."""

from __future__ import annotations

import json as json_module
from collections.abc import AsyncIterator, Mapping, Sequence
from time import monotonic
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from .base import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    HealthStatus,
    InferenceConnectionError,
    InferenceError,
    InferenceHTTPError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    JsonValue,
)

_BACKEND = "ollama"
_VALID_ROLES = frozenset({"system", "user", "assistant", "tool"})
_MAX_STREAM_LINE_BYTES = 1_000_000
_MAX_STREAM_ERROR_BYTES = 16_384


class OllamaBackend:
    """Ollama adapter using ``/api/chat``, ``/api/embed``, and ``/api/tags``."""

    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        think: bool | None = None,
        timeout: float | httpx.Timeout = 30.0,
        health_timeout: float | httpx.Timeout = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        self._chat_model = _validate_model(chat_model, field="chat_model")
        self._embedding_model = _validate_model(
            embedding_model,
            field="embedding_model",
        )
        self._think = think
        self._timeout = _validate_timeout(timeout)
        self._health_timeout = _validate_timeout(health_timeout)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
        )

    async def __aenter__(self) -> OllamaBackend:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close only a client created by this adapter."""

        if self._owns_client:
            await self._client.aclose()

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        normalized_messages = _validate_messages(messages)
        selected_model = _validate_model(model, field="model") if model else self._chat_model
        payload = _chat_payload(
            messages=normalized_messages,
            model=selected_model,
            options=options,
            think=self._think,
            stream=False,
        )

        data = await self._request_json("POST", "/api/chat", operation="chat", json=payload)
        message = data.get("message")
        if not isinstance(message, Mapping):
            raise _response_error("Chat response is missing an object 'message'.", "chat")
        content = message.get("content")
        if not isinstance(content, str):
            raise _response_error("Chat response message has no string 'content'.", "chat")

        response_model = _response_model(data, selected_model, operation="chat")
        done_reason = data.get("done_reason")
        if done_reason is not None and not isinstance(done_reason, str):
            raise _response_error("Chat response has an invalid 'done_reason'.", "chat")
        if data.get("done") is not True:
            raise _response_error("Chat response is not marked complete.", "chat")
        if done_reason == "length":
            raise _response_error("Chat response was truncated by the generation limit.", "chat")
        if self._think is False:
            content = _strip_thinking_trace(content)
            folded_content = content.casefold()
            if "<think>" in folded_content or "</think>" in folded_content:
                raise _response_error("Chat response contains a residual thinking marker.", "chat")
        if not content.strip():
            raise _response_error("Chat response has empty content.", "chat")

        return ChatResponse(
            content=content,
            model=response_model,
            done_reason=done_reason,
            prompt_tokens=_optional_nonnegative_int(data, "prompt_eval_count", "chat"),
            completion_tokens=_optional_nonnegative_int(data, "eval_count", "chat"),
        )

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Stream a single Ollama chat response as validated NDJSON chunks."""

        normalized_messages = _validate_messages(messages)
        selected_model = _validate_model(model, field="model") if model else self._chat_model
        payload = _chat_payload(
            messages=normalized_messages,
            model=selected_model,
            options=options,
            think=self._think,
            stream=True,
        )

        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout,
            ) as response:
                if not 200 <= response.status_code < 300:
                    status_code = response.status_code
                    detail = await _safe_stream_error_detail(response)
                    raise InferenceHTTPError(
                        f"Ollama chat_stream failed with HTTP {status_code}: {detail}",
                        backend=_BACKEND,
                        operation="chat_stream",
                        status_code=status_code,
                        retryable=status_code in {408, 425, 429} or status_code >= 500,
                    )

                saw_done = False
                established_model: str | None = None
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if len(line.encode("utf-8")) > _MAX_STREAM_LINE_BYTES:
                        raise _response_error(
                            "Ollama chat stream emitted an oversized NDJSON line.",
                            "chat_stream",
                        )
                    try:
                        data = json_module.loads(line)
                    except ValueError as exc:
                        raise _response_error(
                            "Ollama chat stream returned invalid NDJSON.",
                            "chat_stream",
                        ) from exc
                    if not isinstance(data, dict):
                        raise _response_error(
                            "Ollama chat stream returned a non-object JSON value.",
                            "chat_stream",
                        )

                    response_model = _response_model(
                        data,
                        established_model or selected_model,
                        operation="chat_stream",
                    )
                    if established_model is None:
                        established_model = response_model
                    elif not (_model_aliases(established_model) & _model_aliases(response_model)):
                        raise _response_error(
                            "Ollama chat stream changed models during one response.",
                            "chat_stream",
                        )

                    message = data.get("message")
                    if not isinstance(message, Mapping):
                        raise _response_error(
                            "Ollama chat stream chunk is missing an object 'message'.",
                            "chat_stream",
                        )
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise _response_error(
                            "Ollama chat stream chunk has invalid content.",
                            "chat_stream",
                        )

                    done = data.get("done")
                    if not isinstance(done, bool):
                        raise _response_error(
                            "Ollama chat stream chunk has an invalid 'done' flag.",
                            "chat_stream",
                        )
                    if saw_done:
                        raise _response_error(
                            "Ollama chat stream emitted data after completion.",
                            "chat_stream",
                        )
                    done_reason = data.get("done_reason")
                    if done_reason is not None and not isinstance(done_reason, str):
                        raise _response_error(
                            "Ollama chat stream chunk has an invalid 'done_reason'.",
                            "chat_stream",
                        )
                    if done_reason == "length":
                        raise _response_error(
                            "Chat response was truncated by the generation limit.",
                            "chat_stream",
                        )
                    if done:
                        saw_done = True

                    yield ChatStreamChunk(
                        content=content,
                        model=response_model,
                        done=done,
                        done_reason=done_reason,
                        prompt_tokens=_optional_nonnegative_int(
                            data,
                            "prompt_eval_count",
                            "chat_stream",
                        ),
                        completion_tokens=_optional_nonnegative_int(
                            data,
                            "eval_count",
                            "chat_stream",
                        ),
                    )

                if not saw_done:
                    raise _response_error(
                        "Ollama chat stream ended without a terminal chunk.",
                        "chat_stream",
                    )
        except InferenceError:
            raise
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(
                "Ollama chat stream timed out.",
                backend=_BACKEND,
                operation="chat_stream",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise InferenceConnectionError(
                "Ollama chat stream could not reach the backend.",
                backend=_BACKEND,
                operation="chat_stream",
                retryable=True,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise InferenceRequestError(
                "Ollama chat stream request could not be encoded.",
                backend=_BACKEND,
                operation="chat_stream",
            ) from exc

    async def health(self) -> HealthStatus:
        """Probe Ollama and verify that both configured mandatory models exist."""

        started = monotonic()
        try:
            data = await self._request_json(
                "GET",
                "/api/tags",
                operation="health",
                request_timeout=self._health_timeout,
            )
            raw_models = data.get("models")
            if not isinstance(raw_models, list):
                raise _response_error("Health response is missing list 'models'.", "health")
        except InferenceError as exc:
            return HealthStatus(
                backend=_BACKEND,
                backend_reachable=not isinstance(
                    exc,
                    (InferenceConnectionError, InferenceTimeoutError),
                ),
                chat_model_ready=False,
                embedding_model_ready=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code=exc.code,
            )

        available_models = _available_model_aliases(raw_models)
        chat_model_ready = bool(_model_aliases(self._chat_model) & available_models)
        embedding_model_ready = bool(_model_aliases(self._embedding_model) & available_models)
        return HealthStatus(
            backend=_BACKEND,
            backend_reachable=True,
            chat_model_ready=chat_model_ready,
            embedding_model_ready=embedding_model_ready,
            latency_ms=(monotonic() - started) * 1000,
            error_code=(
                None if chat_model_ready and embedding_model_ready else "required_models_missing"
            ),
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        json: Mapping[str, Any] | None = None,
        request_timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                json=json,
                timeout=request_timeout or self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(
                f"Ollama {operation} request timed out.",
                backend=_BACKEND,
                operation=operation,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise InferenceConnectionError(
                f"Ollama {operation} request could not reach the backend.",
                backend=_BACKEND,
                operation=operation,
                retryable=True,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise InferenceRequestError(
                f"Ollama {operation} request could not be encoded.",
                backend=_BACKEND,
                operation=operation,
            ) from exc

        if not 200 <= response.status_code < 300:
            status_code = response.status_code
            detail = _safe_error_detail(response)
            raise InferenceHTTPError(
                f"Ollama {operation} failed with HTTP {status_code}: {detail}",
                backend=_BACKEND,
                operation=operation,
                status_code=status_code,
                retryable=status_code in {408, 425, 429} or status_code >= 500,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise _response_error(
                f"Ollama {operation} returned invalid JSON.",
                operation,
            ) from exc
        if not isinstance(data, dict):
            raise _response_error(
                f"Ollama {operation} returned a non-object JSON response.",
                operation,
            )
        return cast(dict[str, Any], data)


def _chat_payload(
    *,
    messages: Sequence[ChatMessage],
    model: str,
    options: Mapping[str, JsonValue] | None,
    think: bool | None,
    stream: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": message.role, "content": message.content} for message in messages
        ],
        "stream": stream,
    }
    if think is not None:
        payload["think"] = think
    if options is not None:
        if not isinstance(options, Mapping):
            raise InferenceRequestError(
                "Chat options must be a mapping.",
                backend=_BACKEND,
                operation="chat_stream" if stream else "chat",
            )
        payload["options"] = dict(options)
    return payload


def _response_model(
    data: Mapping[str, Any],
    selected_model: str,
    *,
    operation: str,
) -> str:
    response_model = data.get("model", selected_model)
    if not isinstance(response_model, str) or not response_model.strip():
        raise _response_error("Chat response has an invalid 'model'.", operation)
    if not (_model_aliases(response_model) & _model_aliases(selected_model)):
        raise _response_error(
            "Chat response model does not match the requested model.",
            operation,
        )
    return response_model.strip()


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url must be a non-empty HTTP(S) URL")
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("base_url must be an HTTP(S) origin without credentials or query data")
    return normalized


def _strip_thinking_trace(content: str) -> str:
    """Strip one well-formed leading trace leaked despite Ollama ``think=false``."""

    leading_trimmed = content.lstrip()
    folded = leading_trimmed.casefold()
    opening = "<think>"
    closing = "</think>"
    if not folded.startswith(opening):
        return content
    closing_index = folded.find(closing, len(opening))
    if closing_index == -1:
        return content
    trace = folded[len(opening) : closing_index]
    if opening in trace or closing in trace:
        return content
    return leading_trimmed[closing_index + len(closing) :].lstrip()


def _validate_model(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _model_aliases(value: str) -> set[str]:
    """Return canonical aliases for Ollama's short and registry-qualified names."""

    normalized = value.strip().casefold().rstrip("/")
    if not normalized:
        return set()
    library_prefix = "registry.ollama.ai/library/"
    canonical = (
        normalized[len(library_prefix) :] if normalized.startswith(library_prefix) else normalized
    )
    names = {normalized, canonical}
    aliases: set[str] = set()
    for name in names:
        aliases.add(name)
        if name.endswith(":latest"):
            aliases.add(name[: -len(":latest")])
        elif ":" not in name:
            aliases.add(f"{name}:latest")
    return aliases


def _available_model_aliases(raw_models: list[Any]) -> set[str]:
    aliases: set[str] = set()
    for raw_model in raw_models:
        if isinstance(raw_model, str):
            aliases.update(_model_aliases(raw_model))
            continue
        if not isinstance(raw_model, Mapping):
            continue
        for key in ("name", "model"):
            value = raw_model.get(key)
            if isinstance(value, str):
                aliases.update(_model_aliases(value))
    return aliases


def _validate_timeout(value: float | httpx.Timeout) -> httpx.Timeout:
    if isinstance(value, httpx.Timeout):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError("timeout must be a positive number or httpx.Timeout")
    return httpx.Timeout(float(value))


def _validate_messages(messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    if isinstance(messages, (str, bytes)) or not isinstance(messages, Sequence):
        raise InferenceRequestError(
            "Messages must be a sequence of ChatMessage values.",
            backend=_BACKEND,
            operation="chat",
        )
    normalized = tuple(messages)
    if not normalized:
        raise InferenceRequestError(
            "At least one chat message is required.",
            backend=_BACKEND,
            operation="chat",
        )
    for message in normalized:
        if not isinstance(message, ChatMessage):
            raise InferenceRequestError(
                "Every message must be a ChatMessage.",
                backend=_BACKEND,
                operation="chat",
            )
        if message.role not in _VALID_ROLES or not isinstance(message.content, str):
            raise InferenceRequestError(
                "Every message must have a supported role and string content.",
                backend=_BACKEND,
                operation="chat",
            )
    return normalized


def _optional_nonnegative_int(
    data: Mapping[str, Any],
    key: str,
    operation: str,
) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _response_error(f"Response has an invalid '{key}'.", operation)
    return cast(int, value)


def _safe_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, Mapping):
        for key in ("error", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:200]
    return response.reason_phrase or "request failed"


async def _safe_stream_error_detail(response: httpx.Response) -> str:
    collected = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = _MAX_STREAM_ERROR_BYTES - len(collected)
        if remaining <= 0:
            break
        collected.extend(chunk[:remaining])
        if len(collected) >= _MAX_STREAM_ERROR_BYTES:
            break
    try:
        data = json_module.loads(collected.decode("utf-8", errors="replace"))
    except ValueError:
        data = None
    if isinstance(data, Mapping):
        for key in ("error", "message"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())[:200]
    return response.reason_phrase or "request failed"


def _response_error(message: str, operation: str) -> InferenceResponseError:
    return InferenceResponseError(
        message,
        backend=_BACKEND,
        operation=operation,
        retryable=False,
    )
