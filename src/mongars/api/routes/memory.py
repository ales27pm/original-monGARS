from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from mongars.api.dependencies import (
    EmbeddingsDependency,
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import (
    MemoryDocumentCreateRequest,
    MemoryDocumentResponse,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResponse,
    TaskResponse,
)
from mongars.embeddings.errors import EmbeddingError
from mongars.events.repository import EventRepository
from mongars.memory.repository import MemoryRepository
from mongars.memory.service import MemoryService
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskService

router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.post("/documents", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_document(
    request: MemoryDocumentCreateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    service = TaskService(
        settings=settings,
        repository=TaskRepository(session),
        events=EventRepository(session),
        policy=policy,
    )
    task = await service.create(
        owner_id=principal.subject,
        kind="memory.note.create",
        payload=request.model_dump(mode="json"),
    )
    return TaskResponse.from_model(task)


@router.get("/documents/{document_id}", response_model=MemoryDocumentResponse)
async def get_document(
    document_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> MemoryDocumentResponse:
    document = await MemoryRepository(session).get_document(
        owner_id=principal.subject, document_id=document_id
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return MemoryDocumentResponse.from_model(document)


@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    request: MemorySearchRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    embeddings: EmbeddingsDependency,
) -> MemorySearchResponse:
    service = MemoryService(
        settings=settings,
        repository=MemoryRepository(session),
        embeddings=embeddings,
    )
    try:
        hits = await service.search(
            owner_id=principal.subject,
            query=request.query,
            top_k=request.top_k,
            hybrid=request.mode == "hybrid",
        )
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "retryable": exc.retryable},
        ) from exc
    return MemorySearchResponse(hits=[MemorySearchHit.from_hit(hit) for hit in hits])
