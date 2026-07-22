from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from mongars.api.dependencies import (
    InferenceDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import ChatRequest, ChatResponse
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
) -> ChatResponse:
    cortex = Cortex(settings=settings, inference=inference, session=session)
    try:
        result = await cortex.chat(
            owner_id=principal.subject,
            message=request.message,
            session_id=request.session_id,
            require_local_only=request.require_local_only,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except InferenceError as exc:
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
    )
