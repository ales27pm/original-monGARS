"""Request-scoped observation of inference streams without changing Bouche authority."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence

from mongars.inference.base import (
    ChatMessage,
    ChatResponse,
    ChatStreamCompleted,
    ChatStreamDelta,
    HealthStatus,
    InferenceBackend,
    InferenceResponseError,
    JsonValue,
    StreamingInferenceBackend,
)

type AttemptCallback = Callable[[int], Awaitable[None]]
type DeltaCallback = Callable[[str], Awaitable[None]]

_THINKING = re.compile(r"</?think\b", re.IGNORECASE)
_DEFAULT_MAX_VISIBLE_BYTES = 1_000_000


class ObservedStreamingInference:
    """Expose backend deltas while preserving the ordinary InferenceBackend contract.

    Bouche still receives one normalized ChatResponse and remains responsible for final
    validation. A second Bouche invocation is surfaced as a new attempt so transports can
    discard provisional text from the first draft.
    """

    def __init__(
        self,
        backend: InferenceBackend,
        *,
        on_attempt: AttemptCallback,
        on_delta: DeltaCallback,
        maximum_visible_bytes: int = _DEFAULT_MAX_VISIBLE_BYTES,
    ) -> None:
        if (
            isinstance(maximum_visible_bytes, bool)
            or not isinstance(maximum_visible_bytes, int)
            or maximum_visible_bytes <= 0
        ):
            raise ValueError("maximum_visible_bytes must be a positive integer")
        self._backend = backend
        self._on_attempt = on_attempt
        self._on_delta = on_delta
        self._maximum_visible_bytes = maximum_visible_bytes
        self._attempt_count = 0

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        self._attempt_count += 1
        await self._on_attempt(self._attempt_count)

        if isinstance(self._backend, StreamingInferenceBackend):
            response: ChatResponse | None = None
            streamed_parts: list[str] = []
            visible_bytes = 0
            async for event in self._backend.stream_chat(
                messages,
                model=model,
                options=options,
            ):
                if isinstance(event, ChatStreamDelta):
                    visible_bytes = await self._emit_delta(
                        event.content,
                        visible_bytes=visible_bytes,
                    )
                    streamed_parts.append(event.content)
                    continue
                if response is not None:
                    raise _stream_error("inference stream emitted multiple completion events")
                response = event.response

            if response is None:
                raise _stream_error("inference stream ended without a completion event")
            if "".join(streamed_parts) != response.content:
                raise _stream_error("inference stream deltas do not match the final response")
            return response

        response = await self._backend.chat(messages, model=model, options=options)
        await self._emit_delta(response.content, visible_bytes=0)
        return response

    async def health(self) -> HealthStatus:
        return await self._backend.health()

    async def aclose(self) -> None:
        """Do not close the shared application backend from a request-scoped wrapper."""

    async def _emit_delta(self, content: str, *, visible_bytes: int) -> int:
        if not isinstance(content, str):
            raise _stream_error("inference stream emitted a non-string delta")
        if not content:
            return visible_bytes
        if _THINKING.search(content) is not None:
            raise _stream_error("inference stream contains a hidden-reasoning marker")
        next_visible_bytes = visible_bytes + len(content.encode("utf-8"))
        if next_visible_bytes > self._maximum_visible_bytes:
            raise _stream_error("inference stream exceeds the visible response byte limit")
        await self._on_delta(content)
        return next_visible_bytes


def _stream_error(message: str) -> InferenceResponseError:
    return InferenceResponseError(
        message,
        backend="stream-observer",
        operation="chat",
        retryable=False,
    )


__all__ = [
    "AttemptCallback",
    "DeltaCallback",
    "ObservedStreamingInference",
]
