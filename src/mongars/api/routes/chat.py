from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.repository import (
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.api.chat_schemas import ChatCitation, TypedChatResponse
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
from mongars.orchestrator.cortex import Cortex
from mongars.orchestrator.typed_chat import TypedChatRuntime

router = APIRouter(prefix="/v1", tags=["cortex"])


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
    try:
        personality = await PersonalityRepository(session).current_snapshot(
            owner_id=principal.subject
        )
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc

    if isinstance(session, AsyncSession):
        runtime: Cortex | TypedChatRuntime = TypedChatRuntime(
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            session=session,
            personality=personality,
            web_search=web_search,
        )
    else:
        # Lightweight focused API tests use a minimal session double. PostgreSQL-backed
        # application sessions always take the typed persistence path above.
        runtime = Cortex(
            settings=settings,
            inference=inference,
            embeddings=embeddings,
            session=session,
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

    citations = tuple(getattr(result, "citations", ()))
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
