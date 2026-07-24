"""Streaming extension for Ollama that never exposes hidden reasoning."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

import httpx

from mongars.inference.base import (
    ChatMessage,
    ChatResponse,
    ChatStreamCompleted,
    ChatStreamDelta,
    ChatStreamEvent,
    InferenceConnectionError,
    InferenceError,
    InferenceHTTPError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    JsonValue,
)
from mongars.inference.ollama import (
    OllamaBackend,
    _optional_nonnegative_int,
    _safe_error_detail,
    _validate_messages,
    _validate_model,
)

_BACKEND = "ollama"
_MAX_STREAM_LINE_BYTES = 1_000_000
_MAX_SUPPRESSED_REASONING_BYTES = 1_000_000
_OPENING_MARKER = "<think>"
_MARKER_PREFIX = "<think"
_CLOSING_MARKER = "</think>"


class StreamingOllamaBackend(OllamaBackend):
    """Ollama backend with bounded NDJSON chat streaming."""

    async def stream_chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamEvent]:
        normalized_messages = _validate_messages(messages)
        selected_model = _validate_model(model, field="model") if model else self._chat_model
        payload: dict[str, Any] = {
            "model": selected_model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in normalized_messages
            ],
            "stream": True,
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

        visible_filter = _VisibleContentFilter()
        visible_parts: list[str] = []
        completed: dict[str, Any] | None = None
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=self._timeout,
            ) as response:
                if not 200 <= response.status_code < 300:
                    await response.aread()
                    status_code = response.status_code
                    detail = _safe_error_detail(response)
                    raise InferenceHTTPError(
                        f"Ollama chat failed with HTTP {status_code}: {detail}",
                        backend=_BACKEND,
                        operation="chat",
                        status_code=status_code,
                        retryable=status_code in {408, 425, 429} or status_code >= 500,
                    )

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if len(line.encode("utf-8")) > _MAX_STREAM_LINE_BYTES:
                        raise _response_error("Ollama chat stream frame exceeds the byte limit.")
                    try:
                        raw = json.loads(line)
                    except ValueError as exc:
                        raise _response_error("Ollama chat stream returned invalid JSON.") from exc
                    if not isinstance(raw, dict):
                        raise _response_error("Ollama chat stream returned a non-object frame.")
                    data = cast(dict[str, Any], raw)
                    stream_error = data.get("error")
                    if isinstance(stream_error, str) and stream_error.strip():
                        raise InferenceResponseError(
                            "Ollama chat stream reported a generation error.",
                            backend=_BACKEND,
                            operation="chat",
                            retryable=True,
                        )

                    message = data.get("message")
                    if message is not None:
                        if not isinstance(message, Mapping):
                            raise _response_error(
                                "Ollama chat stream frame has an invalid message object."
                            )
                        content = message.get("content", "")
                        if not isinstance(content, str):
                            raise _response_error(
                                "Ollama chat stream frame has non-string content."
                            )
                        # Ollama may emit a separate `thinking` field. It is deliberately ignored.
                        for visible in visible_filter.feed(content):
                            visible_parts.append(visible)
                            yield ChatStreamDelta(content=visible)

                    if data.get("done") is True:
                        completed = data
                        break
        except InferenceError:
            raise
        except httpx.TimeoutException as exc:
            raise InferenceTimeoutError(
                "Ollama chat stream timed out.",
                backend=_BACKEND,
                operation="chat",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise InferenceConnectionError(
                "Ollama chat stream could not reach the backend.",
                backend=_BACKEND,
                operation="chat",
                retryable=True,
            ) from exc
        except (TypeError, ValueError) as exc:
            raise InferenceRequestError(
                "Ollama chat stream request could not be encoded.",
                backend=_BACKEND,
                operation="chat",
            ) from exc

        if completed is None:
            raise _response_error("Ollama chat stream ended before a completion frame.")
        tail = visible_filter.finish()
        if tail:
            visible_parts.append(tail)
            yield ChatStreamDelta(content=tail)

        content = "".join(visible_parts)
        if not content.strip():
            raise _response_error("Ollama chat stream has empty content.")
        done_reason = completed.get("done_reason")
        if done_reason is not None and not isinstance(done_reason, str):
            raise _response_error("Ollama chat stream has an invalid done_reason.")
        if done_reason == "length":
            raise _response_error("Ollama chat stream was truncated by the generation limit.")
        response_model = completed.get("model", selected_model)
        if not isinstance(response_model, str) or not response_model.strip():
            raise _response_error("Ollama chat stream has an invalid model.")

        yield ChatStreamCompleted(
            response=ChatResponse(
                content=content,
                model=response_model,
                done_reason=done_reason,
                prompt_tokens=_optional_nonnegative_int(
                    completed,
                    "prompt_eval_count",
                    "chat",
                ),
                completion_tokens=_optional_nonnegative_int(
                    completed,
                    "eval_count",
                    "chat",
                ),
            )
        )


class _VisibleContentFilter:
    """Suppress one leading marker-delimited trace and reject later markers."""

    def __init__(self) -> None:
        self._buffer = ""
        self._state = "leading"

    def feed(self, content: str) -> tuple[str, ...]:
        self._buffer += content
        emitted: list[str] = []
        while True:
            if self._state == "leading":
                start = len(self._buffer) - len(self._buffer.lstrip())
                candidate = self._buffer[start:].casefold()
                if not candidate:
                    break
                if _OPENING_MARKER.startswith(candidate) and len(candidate) < len(
                    _OPENING_MARKER
                ):
                    break
                if candidate.startswith(_OPENING_MARKER):
                    self._buffer = self._buffer[start + len(_OPENING_MARKER) :]
                    self._state = "suppressing"
                    continue
                self._state = "visible"
                continue

            if self._state == "suppressing":
                closing_index = self._buffer.casefold().find(_CLOSING_MARKER)
                if closing_index < 0:
                    if len(self._buffer.encode("utf-8")) > _MAX_SUPPRESSED_REASONING_BYTES:
                        raise _response_error(
                            "Ollama chat stream hidden reasoning exceeds the byte limit."
                        )
                    break
                self._buffer = self._buffer[
                    closing_index + len(_CLOSING_MARKER) :
                ].lstrip()
                self._state = "visible"
                continue

            marker_index = self._buffer.casefold().find(_MARKER_PREFIX)
            if marker_index >= 0:
                if marker_index:
                    emitted.append(self._buffer[:marker_index])
                self._buffer = self._buffer[marker_index:]
                raise _response_error(
                    "Ollama chat stream contains a residual thinking marker."
                )
            retained = len(_MARKER_PREFIX) - 1
            if len(self._buffer) <= retained:
                break
            emitted.append(self._buffer[:-retained])
            self._buffer = self._buffer[-retained:]
            break
        return tuple(part for part in emitted if part)

    def finish(self) -> str:
        if self._state == "suppressing":
            raise _response_error("Ollama chat stream contains an unfinished thinking trace.")
        if self._state == "leading":
            candidate = self._buffer.lstrip().casefold()
            if candidate.startswith(_MARKER_PREFIX):
                raise _response_error("Ollama chat stream contains an unfinished thinking trace.")
        if _MARKER_PREFIX in self._buffer.casefold():
            raise _response_error("Ollama chat stream contains a residual thinking marker.")
        result = self._buffer
        self._buffer = ""
        return result


def _response_error(message: str) -> InferenceResponseError:
    return InferenceResponseError(
        message,
        backend=_BACKEND,
        operation="chat",
        retryable=False,
    )


__all__ = ["StreamingOllamaBackend"]
