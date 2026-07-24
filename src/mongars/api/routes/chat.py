from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.repository import (
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.api.chat_schemas import ChatCitation, TypedChatResponse
from mongars.api.chat_streaming import ChatStreamPump, StreamingBouche, cancel_and_join
from mongars.api.dependencies import (
    EmbeddingsDependency,
    InferenceDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
    WebSearchDependency,
)
from mongars.api.schemas import ChatRequest, WebSource
from mongars.embeddings.errors import EmbeddingError, EmbeddingInputError
from mongars.inference.base import InferenceError
from mongars.orchestrator.cortex import ChatResult, Cortex
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.orchestrator.typed_chat import TypedChatResult, TypedChatRuntime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["cortex"])
type RuntimeResult = ChatResult | TypedChatResult


@router.post("/chat", response_model=TypedChatResponse)
async def chat(
    request: ChatRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    inference: InferenceDependency,
    embeddings: EmbeddingsDependency,
    web_search: WebSearchDependency,
) -> TypedChatResponse:
    personality = await _personality(session=session, owner_id=principal.subject)
    runtime = _runtime(
        session=session,
        settings=settings,
        inference=inference,
        embeddings=embeddings,
        personality=personality,
        web_search=web_search,
    )

    try:
        result = await runtime.chat(
            owner_id=principal.subject,
            message=request.message,
            session_id=request.session_id,
            require_local_only=request.require_local_only,
            web_search_mode=request.web_search,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except InferenceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "retryable": exc.retryable},
        ) from exc
    except EmbeddingInputError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "retryable": exc.retryable},
        ) from exc
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "retryable": exc.retryable},
        ) from exc

    return _response(result)


@router.post("/chat/stream", response_class=StreamingResponse)
async def chat_stream(
    request: ChatRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    inference: InferenceDependency,
    embeddings: EmbeddingsDependency,
    web_search: WebSearchDependency,
) -> StreamingResponse:
    """Stream one chat turn as newline-delimited JSON over authenticated HTTPS."""

    _validate_before_stream(request=request, settings=settings)
    personality = await _personality(session=session, owner_id=principal.subject)
    pump = ChatStreamPump()

    if isinstance(session, AsyncSession):
        runtime: Cortex | TypedChatRuntime = TypedChatRuntime(
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            session=session,
            personality=personality,
            web_search=web_search,
            bouche=StreamingBouche(
                inference,
                on_start=pump.on_start,
                on_delta=pump.on_delta,
            ),
        )
    else:
        runtime = Cortex(
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            session=session,
            personality=personality,
            web_search=web_search,
        )

    async def produce() -> None:
        try:
            result = await runtime.chat(
                owner_id=principal.subject,
                message=request.message,
                session_id=request.session_id,
                require_local_only=request.require_local_only,
                web_search_mode=request.web_search,
            )
        except asyncio.CancelledError:
            raise
        except (InferenceError, EmbeddingError) as exc:
            await pump.fail(exc)
        except (ValueError, PermissionError) as exc:
            await pump.fail(_PublicStreamError("invalid_request", retryable=False))
            logger.info(
                "chat_stream_request_rejected",
                extra={"error_type": type(exc).__name__},
            )
        except Exception as exc:
            logger.exception(
                "chat_stream_failed",
                extra={"error_type": type(exc).__name__},
            )
            await pump.fail(_PublicStreamError("stream_error", retryable=False))
        else:
            await pump.finish(result)
        finally:
            await pump.close()

    async def body() -> AsyncIterator[bytes]:
        producer = asyncio.create_task(produce(), name="mongars-chat-stream")
        try:
            async for frame in pump.bytes():
                yield frame
        finally:
            await cancel_and_join(producer)

    return StreamingResponse(
        body(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _personality(
    *,
    session: SessionDependency,
    owner_id: str,
) -> PersonalitySnapshot:
    try:
        return await PersonalityRepository(session).current_snapshot(owner_id=owner_id)
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc


def _runtime(
    *,
    session: SessionDependency,
    settings: SettingsDependency,
    inference: InferenceDependency,
    embeddings: EmbeddingsDependency,
    personality: PersonalitySnapshot,
    web_search: WebSearchDependency,
) -> Cortex | TypedChatRuntime:
    if isinstance(session, AsyncSession):
        return TypedChatRuntime(
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            session=session,
            personality=personality,
            web_search=web_search,
        )
    # Lightweight focused API tests use a minimal session double. PostgreSQL-backed
    # application sessions always take the typed persistence path above.
    return Cortex(
        settings=settings,
        inference=inference,
        embeddings=embeddings,
        session=session,
        personality=personality,
        web_search=web_search,
    )


def _validate_before_stream(*, request: ChatRequest, settings: SettingsDependency) -> None:
    normalized = request.message.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="message must not be empty",
        )
    if len(normalized) > settings.max_chat_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="message exceeds the configured character limit",
        )
    if request.require_local_only and not settings.inference_is_local:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a local inference endpoint is required",
        )


def _response(result: RuntimeResult) -> TypedChatResponse:
    citations = result.citations if isinstance(result, TypedChatResult) else ()
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
            for citation in citations
        ],
    )


class _PublicStreamError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
