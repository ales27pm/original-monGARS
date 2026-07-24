"""Transport-only NDJSON streaming helpers for the typed chat runtime."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Final

from mongars.api.chat_schemas import (
    ChatCitation,
    ChatStreamDelta,
    ChatStreamError,
    ChatStreamFinal,
    ChatStreamFrame,
    ChatStreamSource,
    ChatStreamSources,
    ChatStreamStart,
)
from mongars.api.schemas import WebSource
from mongars.autobiography.contracts import EvidenceSnapshot
from mongars.dialogue import (
    Bouche,
    BoucheStreamDelta,
    BoucheStreamFinal,
    ComposedResponse,
    DialoguePlan,
)
from mongars.inference.base import InferenceBackend, InferenceResponseError
from mongars.orchestrator.cortex import ChatResult
from mongars.orchestrator.typed_chat import TypedChatResult

_MAX_FRAME_BYTES: Final = 1_000_000
_MAX_STREAM_FRAMES: Final = 10_000
_MAX_STREAM_ANSWER_CHARACTERS: Final = 1_000_000
_STREAM_QUEUE_SIZE: Final = 64
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,100}$")
_QUEUE_END: Final = object()
type StreamCallback = Callable[[DialoguePlan], Awaitable[None]]
type DeltaCallback = Callable[[str], Awaitable[None]]
type RuntimeResult = ChatResult | TypedChatResult


class StreamingBouche(Bouche):
    """Adapt Bouche streaming to the ``compose`` seam used by TypedChatRuntime."""

    def __init__(
        self,
        inference: InferenceBackend,
        *,
        on_start: StreamCallback,
        on_delta: DeltaCallback,
    ) -> None:
        super().__init__(inference)
        self._delegate = Bouche(inference)
        self._on_start = on_start
        self._on_delta = on_delta

    async def compose(self, plan: DialoguePlan) -> ComposedResponse:
        await self._on_start(plan)
        final: ComposedResponse | None = None
        async for event in self._delegate.stream(plan):
            if isinstance(event, BoucheStreamDelta):
                await self._on_delta(event.text)
                continue
            if isinstance(event, BoucheStreamFinal):
                if final is not None:
                    raise InferenceResponseError(
                        "Bouche stream emitted multiple final responses.",
                        backend="ollama",
                        operation="chat_stream",
                        retryable=False,
                    )
                final = event.response
        if final is None:
            raise InferenceResponseError(
                "Bouche stream ended without a final response.",
                backend="ollama",
                operation="chat_stream",
                retryable=True,
            )
        return final


class ChatStreamPump:
    """Serialize lifecycle callbacks into a bounded backpressure-aware queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[bytes | object] = asyncio.Queue(
            maxsize=_STREAM_QUEUE_SIZE
        )
        self._started = False
        self._terminal = False
        self._frame_count = 0
        self._delta_characters = 0

    async def on_start(self, plan: DialoguePlan) -> None:
        if self._started:
            raise RuntimeError("chat stream start was emitted more than once")
        self._started = True
        await self._put_frame(
            ChatStreamStart(
                trace_id=plan.trace_id,
                session_id=plan.session_id,
            )
        )
        await self._put_frame(
            ChatStreamSources(
                sources=[
                    _source_from_evidence(item)
                    for item in plan.evidence
                    if item.included
                ]
            )
        )

    async def on_delta(self, text: str) -> None:
        if not self._started:
            raise RuntimeError("chat stream delta arrived before the start frame")
        if not text:
            return
        next_total = self._delta_characters + len(text)
        if next_total > _MAX_STREAM_ANSWER_CHARACTERS:
            raise InferenceResponseError(
                "Bouche stream exceeded the answer-size ceiling.",
                backend="application",
                operation="chat_stream",
                retryable=False,
            )
        self._delta_characters = next_total
        await self._put_frame(ChatStreamDelta(text=text))

    async def finish(self, result: RuntimeResult) -> None:
        if not self._started:
            self._started = True
            await self._put_frame(
                ChatStreamStart(
                    trace_id=result.trace_id,
                    session_id=result.session_id,
                )
            )
            await self._put_frame(ChatStreamSources(sources=[]))
            if result.answer:
                await self.on_delta(result.answer)
        await self._put_frame(_final_frame(result), terminal=True)

    async def fail(self, error: BaseException) -> None:
        if self._terminal:
            return
        raw_code = getattr(error, "code", None)
        code = (
            raw_code
            if isinstance(raw_code, str) and _SAFE_ERROR_CODE.fullmatch(raw_code)
            else "stream_error"
        )
        retryable = getattr(error, "retryable", False)
        await self._put_frame(
            ChatStreamError(
                code=code,
                retryable=bool(retryable),
            ),
            terminal=True,
        )

    async def close(self) -> None:
        await self._queue.put(_QUEUE_END)

    async def bytes(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            if item is _QUEUE_END:
                return
            if not isinstance(item, bytes):
                raise RuntimeError("chat stream queue contained an invalid item")
            yield item

    async def _put_frame(
        self,
        frame: ChatStreamFrame,
        *,
        terminal: bool = False,
    ) -> None:
        if self._terminal:
            raise RuntimeError("chat stream emitted a frame after completion")
        maximum_before_put = (
            _MAX_STREAM_FRAMES if terminal else _MAX_STREAM_FRAMES - 1
        )
        if self._frame_count >= maximum_before_put:
            raise InferenceResponseError(
                "Chat stream exceeded the frame-count ceiling.",
                backend="application",
                operation="chat_stream",
                retryable=False,
            )
        serialized = _serialize_frame(frame)
        self._frame_count += 1
        if terminal:
            self._terminal = True
        await self._queue.put(serialized)


async def cancel_and_join[T](task: asyncio.Task[T]) -> None:
    """Cancel a stream producer and absorb only its expected cancellation."""

    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _serialize_frame(frame: ChatStreamFrame) -> bytes:
    serialized = (
        json.dumps(
            frame.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if len(serialized) > _MAX_FRAME_BYTES:
        raise InferenceResponseError(
            "Chat stream frame exceeds its byte ceiling.",
            backend="application",
            operation="chat_stream",
            retryable=False,
        )
    return serialized


def _source_from_evidence(item: EvidenceSnapshot) -> ChatStreamSource:
    return ChatStreamSource(
        key=item.key,
        kind=item.kind,
        source_id=item.source_id,
        title=item.title,
        url=item.source_uri,
        locator=dict(item.locator) if item.locator is not None else None,
        included=item.included,
    )


def _citation_payload(result: RuntimeResult) -> list[ChatCitation]:
    if not isinstance(result, TypedChatResult):
        return []
    return [
        ChatCitation(
            key=citation.key,
            kind=citation.kind,
            source_id=citation.source_id,
            title=citation.title,
            url=citation.source_uri,
            locator=dict(citation.locator) if citation.locator is not None else None,
        )
        for citation in result.citations
    ]


def _final_frame(result: RuntimeResult) -> ChatStreamFinal:
    return ChatStreamFinal(
        trace_id=result.trace_id,
        session_id=result.session_id,
        answer=result.answer,
        model=result.model,
        memory_hits=result.memory_hits,
        web_search_status=result.web_search_status,
        sources=[WebSource(title=source.title, url=source.url) for source in result.sources],
        citations=_citation_payload(result),
    )


__all__ = [
    "ChatStreamPump",
    "StreamingBouche",
    "cancel_and_join",
]
