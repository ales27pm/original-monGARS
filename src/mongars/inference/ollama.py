"""Typed asynchronous adapter for Ollama's native HTTP API."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from time import monotonic
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from .base import (
    ChatMessage,
    ChatResponse,
    EmbeddingDimensionError,
    EmbeddingResponse,
    HealthStatus,
    InferenceConfigurationError,
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


class OllamaBackend:
    """Ollama adapter using ``/api/chat``, ``/api/embed``, and ``/api/tags``."""

    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        embedding_model: str,
        embedding_dimension: int | None = None,
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
        self._embedding_dimension = _validate_optional_dimension(
            embedding_dimension,
            field="embedding_dimension",
        )
        self._think = think
        self._timeout = _validate_timeout(timeout)
        self._health_timeout = _validate_timeout(health_timeout)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()

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
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in normalized_messages
            ],
            "stream": False,
        }
        if self._think is not None:
            payload["think"] = self._think
        if options is not None:
            if not isinstance(options, Mapping):
                raise InferenceRequestError(
                    "Chat options must be a mapping.",
                    backend=_BACKEND,
                    operation="chat",
                )
            payload["options"] = dict(options)

        data = await self._request_json("POST", "/api/chat", operation="chat", json=payload)
        message = data.get("message")
        if not isinstance(message, Mapping):
            raise _response_error("Chat response is missing an object 'message'.", "chat")
        content = message.get("content")
        if not isinstance(content, str):
            raise _response_error("Chat response message has no string 'content'.", "chat")
        if self._think is False:
            content = _strip_thinking_trace(content)

        response_model = data.get("model", selected_model)
        if not isinstance(response_model, str) or not response_model.strip():
            raise _response_error("Chat response has an invalid 'model'.", "chat")
        done_reason = data.get("done_reason")
        if done_reason is not None and not isinstance(done_reason, str):
            raise _response_error("Chat response has an invalid 'done_reason'.", "chat")
        if data.get("done") is not True:
            raise _response_error("Chat response is not marked complete.", "chat")

        return ChatResponse(
            content=content,
            model=response_model,
            done_reason=done_reason,
            prompt_tokens=_optional_nonnegative_int(data, "prompt_eval_count", "chat"),
            completion_tokens=_optional_nonnegative_int(data, "eval_count", "chat"),
        )

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        model: str | None = None,
        expected_dimension: int | None = None,
    ) -> EmbeddingResponse:
        normalized_inputs = _validate_inputs(inputs)
        selected_model = _validate_model(model, field="model") if model else self._embedding_model
        dimension = _validate_optional_dimension(
            expected_dimension,
            field="expected_dimension",
        )
        if dimension is None:
            dimension = self._embedding_dimension
        if dimension is None:
            raise InferenceConfigurationError(
                "An expected embedding dimension must be supplied by configuration or caller.",
                backend=_BACKEND,
                operation="embed",
            )

        data = await self._request_json(
            "POST",
            "/api/embed",
            operation="embed",
            json={"model": selected_model, "input": list(normalized_inputs)},
        )
        raw_embeddings = data.get("embeddings")
        if not isinstance(raw_embeddings, list):
            raise _response_error("Embedding response is missing list 'embeddings'.", "embed")
        if len(raw_embeddings) != len(normalized_inputs):
            raise _response_error(
                (
                    "Embedding response count does not match input count: "
                    f"received {len(raw_embeddings)}, expected {len(normalized_inputs)}."
                ),
                "embed",
            )

        embeddings: list[tuple[float, ...]] = []
        for index, raw_embedding in enumerate(raw_embeddings):
            if not isinstance(raw_embedding, list):
                raise _response_error(f"Embedding {index} is not a list.", "embed")
            if len(raw_embedding) != dimension:
                raise EmbeddingDimensionError(
                    backend=_BACKEND,
                    expected=dimension,
                    actual=len(raw_embedding),
                    index=index,
                )
            vector: list[float] = []
            for component in raw_embedding:
                if (
                    isinstance(component, bool)
                    or not isinstance(component, (int, float))
                    or not math.isfinite(component)
                ):
                    raise _response_error(
                        f"Embedding {index} contains a non-finite or non-numeric component.",
                        "embed",
                    )
                vector.append(float(component))
            embeddings.append(tuple(vector))

        response_model = data.get("model", selected_model)
        if not isinstance(response_model, str) or not response_model.strip():
            raise _response_error("Embedding response has an invalid 'model'.", "embed")
        return EmbeddingResponse(
            embeddings=tuple(embeddings),
            model=response_model,
            dimension=dimension,
        )

    async def health(self) -> HealthStatus:
        """Probe Ollama's model-list endpoint and convert failures to status data."""

        started = monotonic()
        try:
            data = await self._request_json(
                "GET",
                "/api/tags",
                operation="health",
                request_timeout=self._health_timeout,
            )
            if not isinstance(data.get("models"), list):
                raise _response_error("Health response is missing list 'models'.", "health")
        except InferenceError as exc:
            return HealthStatus(
                backend=_BACKEND,
                healthy=False,
                latency_ms=(monotonic() - started) * 1000,
                error_code=exc.code,
            )
        return HealthStatus(
            backend=_BACKEND,
            healthy=True,
            latency_ms=(monotonic() - started) * 1000,
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
    """Handle models that leak a reasoning prefix despite Ollama ``think=false``."""

    _prefix, marker, final = content.rpartition("</think>")
    return final.lstrip() if marker else content


def _validate_model(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_optional_dimension(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


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


def _validate_inputs(inputs: Sequence[str]) -> tuple[str, ...]:
    if isinstance(inputs, (str, bytes)) or not isinstance(inputs, Sequence):
        raise InferenceRequestError(
            "Embedding inputs must be a sequence of strings.",
            backend=_BACKEND,
            operation="embed",
        )
    normalized = tuple(inputs)
    if not normalized:
        raise InferenceRequestError(
            "At least one embedding input is required.",
            backend=_BACKEND,
            operation="embed",
        )
    if any(not isinstance(value, str) or not value.strip() for value in normalized):
        raise InferenceRequestError(
            "Embedding inputs must be non-empty strings.",
            backend=_BACKEND,
            operation="embed",
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


def _response_error(message: str, operation: str) -> InferenceResponseError:
    return InferenceResponseError(
        message,
        backend=_BACKEND,
        operation=operation,
        retryable=False,
    )
