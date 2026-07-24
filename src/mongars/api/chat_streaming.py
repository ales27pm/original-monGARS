"""Transport-only NDJSON streaming helpers for the typed chat runtime."""

from __future__ import annotations

import asyncio
import json
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
from mongars.orchestrator.typed_chat import TypedChatResult

_MAX_FRAME_BYTES: Final = 1_000_000
_QUEUE_END: Final = object()
type StreamCallback = Callable[[DialoguePlan], Awaitable[None]]
type DeltaCallback = Callable[[str], Awaitable[None]]


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
    """Serialize lifecycle callbacks into a bounded, cancellation-friendly frame queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[ChatStreamFrame | object] = asyncio.Queue()
        self._started = False

    async def on_start(self, plan: DialoguePlan) -> None:
        if self._started:
            raise RuntimeError("chat stream start was emitted more than once")
        self._started = True
        await self._queue.put(
            ChatStreamStart(
                trace_id=plan.trace_id,
                session_id=plan.session_id,
            )
        )
        await self._queue.put(
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
        if text:
            await self._queue.put(ChatStreamDelta(text=text))

    async def finish(self, result: TypedChatResult) -> None:
        if not self._started:
            self._started = True
            await self._queue.put(
                ChatStreamStart(
                    trace_id=result.trace_id,
                    session_id=result.session_id,
                )
            )
            await self._queue.put(ChatStreamSources(sources=[]))
            if result.answer:
                await self._queue.put(ChatStreamDelta(text=result.answer))
        await self._queue.put(_final_frame(result))

    async def fail(self, error: BaseException) -> None:
        code = getattr(error, "code", None)
        retryable = getattr(error, "retryable", False)
        await self._queue.put(
            ChatStreamError(
                code=code if isinstance(code, str) and code else "stream_error",
                retryable=bool(retryable),
            )
        )

    async def close(self) -> None:
        await self._queue.put(_QUEUE_END)

    async def bytes(self) -> AsyncIterator[bytes]:
        while True:
            item = await self._queue.get()
            if item is _QUEUE_END:
                return
            if not isinstance(
                item,
                (
                    ChatStreamStart,
                    ChatStreamSources,
                    ChatStreamDelta,
                    ChatStreamFinal,
                    ChatStreamError,
                ),
            ):
                raise RuntimeError("chat stream queue contained an invalid item")
            serialized = (
                json.dumps(
                    item.model_dump(mode="json"),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            if len(serialized) > _MAX_FRAME_BYTES:
                raise RuntimeError("chat stream frame exceeds its hard byte ceiling")
            yield serialized


async def cancel_and_join[T](task: asyncio.Task[T]) -> None:
    """Cancel a stream producer and absorb only its expected cancellation."""

    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError):
        await task


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


def _citation_payload(result: TypedChatResult) -> list[ChatCitation]:
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


def _final_frame(result: TypedChatResult) -> ChatStreamFinal:
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
