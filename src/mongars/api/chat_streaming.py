"""Authenticated NDJSON transport for provisional Bouche text and validated results."""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import suppress

from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.api.chat_schemas import ChatCitation, TypedChatResponse
from mongars.api.schemas import ChatRequest, WebSource
from mongars.config import Settings
from mongars.embeddings.errors import EmbeddingError, EmbeddingInputError
from mongars.embeddings.service import EmbeddingService
from mongars.inference.base import InferenceBackend, InferenceError, JsonValue
from mongars.inference.streaming import ObservedStreamingInference
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.orchestrator.typed_chat import TypedChatResult, TypedChatRuntime
from mongars.web_search import SearxNGSearchBackend

_STREAM_PROTOCOL = "mongars-chat-ndjson-v1"
_STREAM_QUEUE_SIZE = 64


def typed_chat_response(result: TypedChatResult) -> TypedChatResponse:
    """Convert the internal result to the only authoritative public response shape."""

    return TypedChatResponse(
        trace_id=result.trace_id,
        session_id=result.session_id,
        answer=result.answer,
        model=result.model,
        memory_hits=result.memory_hits,
        web_search_status=result.web_search_status,
        sources=[WebSource(title=source.title, url=source.url) for source in result.sources],
        citations=[
            ChatCitation(
                key=citation.key,
                kind=citation.kind,
                source_id=citation.source_id,
                title=citation.title,
                url=citation.source_uri,
                locator=(dict(citation.locator) if citation.locator is not None else None),
            )
            for citation in result.citations
        ],
    )


def build_typed_chat_stream(
    *,
    request: ChatRequest,
    owner_id: str,
    session: AsyncSession,
    settings: Settings,
    inference: InferenceBackend,
    embeddings: EmbeddingService,
    personality: PersonalitySnapshot | None,
    web_search: SearxNGSearchBackend | None,
) -> StreamingResponse:
    """Create a no-store stream whose final frame is backed by durable typed state."""

    return StreamingResponse(
        _stream_frames(
            request=request,
            owner_id=owner_id,
            session=session,
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            personality=personality,
            web_search=web_search,
        ),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_frames(
    *,
    request: ChatRequest,
    owner_id: str,
    session: AsyncSession,
    settings: Settings,
    inference: InferenceBackend,
    embeddings: EmbeddingService,
    personality: PersonalitySnapshot | None,
    web_search: SearxNGSearchBackend | None,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[dict[str, JsonValue] | None] = asyncio.Queue(
        maxsize=_STREAM_QUEUE_SIZE
    )
    stream_id = f"str_{secrets.token_hex(16)}"
    current_attempt = 0

    async def on_attempt(index: int) -> None:
        nonlocal current_attempt
        current_attempt = index
        if index > 1:
            await queue.put(
                {
                    "type": "reset",
                    "attempt": index,
                    "reason": "validation_retry",
                }
            )
        await queue.put({"type": "attempt", "attempt": index})

    async def on_delta(text: str) -> None:
        await queue.put(
            {
                "type": "delta",
                "attempt": current_attempt,
                "text": text,
            }
        )

    observed = ObservedStreamingInference(
        inference,
        on_attempt=on_attempt,
        on_delta=on_delta,
    )
    runtime = TypedChatRuntime(
        settings=settings,
        inference=observed,
        embeddings=embeddings,
        session=session,
        personality=personality,
        web_search=web_search,
    )

    async def execute() -> None:
        try:
            result = await runtime.chat(
                owner_id=owner_id,
                message=request.message,
                session_id=request.session_id,
                require_local_only=request.require_local_only,
                web_search_mode=request.web_search,
            )
            response = typed_chat_response(result)
            await queue.put(
                {
                    "type": "final",
                    "response": response.model_dump(mode="json"),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put(_error_frame(exc))
        finally:
            with suppress(asyncio.CancelledError):
                await queue.put(None)

    runtime_task = asyncio.create_task(execute())
    yield _encode_frame(
        {
            "type": "start",
            "protocol": _STREAM_PROTOCOL,
            "stream_id": stream_id,
        }
    )
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield _encode_frame(frame)
    finally:
        if not runtime_task.done():
            runtime_task.cancel()
        await asyncio.gather(runtime_task, return_exceptions=True)


def _error_frame(exc: Exception) -> dict[str, JsonValue]:
    if isinstance(exc, InferenceError):
        code = exc.code
        retryable = exc.retryable
    elif isinstance(exc, EmbeddingInputError):
        code = exc.code
        retryable = exc.retryable
    elif isinstance(exc, EmbeddingError):
        code = exc.code
        retryable = exc.retryable
    elif isinstance(exc, (ValueError, PermissionError)):
        code = "invalid_request"
        retryable = False
    else:
        code = "internal_error"
        retryable = False
    return {
        "type": "error",
        "code": code,
        "retryable": retryable,
        "discard_partial": True,
    }


def _encode_frame(frame: dict[str, JsonValue]) -> bytes:
    return (
        json.dumps(
            frame,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


__all__ = ["build_typed_chat_stream", "typed_chat_response"]
