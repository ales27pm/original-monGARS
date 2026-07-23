from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from mongars.adaptation.repository import (
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.api.dependencies import (
    EmbeddingsDependency,
    InferenceDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
    WebSearchDependency,
)
from mongars.api.schemas import ChatRequest, ChatResponse, WebSource
from mongars.embeddings.errors import EmbeddingError, EmbeddingInputError
from mongars.inference.base import InferenceError
from mongars.orchestrator.cortex import Cortex

router = APIRouter(prefix="/v1", tags=["cortex"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    inference: InferenceDependency,
    embeddings: EmbeddingsDependency,
    web_search: WebSearchDependency,
) -> ChatResponse:
    try:
        personality = await PersonalityRepository(session).current_snapshot(
            owner_id=principal.subject
        )
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc

    cortex = Cortex(
        settings=settings,
        inference=inference,
        embeddings=embeddings,
        session=session,
        personality=personality,
        web_search=web_search,
    )
    try:
        result = await cortex.chat(
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
    return ChatResponse(
        trace_id=result.trace_id,
        session_id=result.session_id,
        answer=result.answer,
        model=result.model,
        memory_hits=result.memory_hits,
        web_search_status=result.web_search_status,
        sources=[WebSource(title=source.title, url=source.url) for source in result.sources],
    )
