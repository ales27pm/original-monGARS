from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from mongars.api.dependencies import (
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import (
    TaskApproveRequest,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskPayloadPageResponse,
    TaskResponse,
)
from mongars.events.repository import EventRepository
from mongars.rm.contracts import UnsupportedTaskKind
from mongars.rm.payload_view import task_payload_page
from mongars.rm.repository import TaskRepository
from mongars.rm.service import (
    TaskIntegrityError,
    TaskReviewMismatchError,
    TaskService,
    TaskStateError,
)

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _service(
    *,
    settings: SettingsDependency,
    session: SessionDependency,
    policy: PolicyDependency,
) -> TaskService:
    return TaskService(
        settings=settings,
        repository=TaskRepository(session),
        events=EventRepository(session),
        policy=policy,
    )


@router.post("", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_task(
    request: TaskCreateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    service = _service(settings=settings, session=session, policy=policy)
    try:
        task = await service.create(
            owner_id=principal.subject,
            kind=request.kind,
            payload=request.payload,
            priority=request.priority,
            max_attempts=request.max_attempts,
            dedupe_key=request.dedupe_key,
        )
    except (UnsupportedTaskKind, ValidationError, PermissionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="duplicate task") from exc
    return TaskResponse.from_model(task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    principal: PrincipalDependency,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TaskResponse]:
    tasks = await TaskRepository(session).list_for_owner(owner_id=principal.subject, limit=limit)
    return [TaskResponse.from_model(task) for task in tasks]


@router.get("/{task_id}", response_model=TaskDetailResponse)
async def get_task(
    task_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetailResponse:
    task = await TaskRepository(session).get_for_owner(task_id=task_id, owner_id=principal.subject)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    await session.refresh(task)
    return TaskDetailResponse.from_model(task)


@router.get("/{task_id}/payload", response_model=TaskPayloadPageResponse)
async def get_task_payload_page(
    task_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> TaskPayloadPageResponse:
    task = await TaskRepository(session).get_for_owner(
        task_id=task_id,
        owner_id=principal.subject,
    )
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    await session.refresh(task)
    try:
        rendered_page = task_payload_page(task.payload, page_index=page)
    except IndexError as exc:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="payload page is out of range",
        ) from exc
    return TaskPayloadPageResponse.from_rendered(task=task, page=rendered_page)


@router.post("/{task_id}/approve", response_model=TaskResponse)
async def approve_task(
    task_id: UUID,
    request: TaskApproveRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    service = _service(settings=settings, session=session, policy=policy)
    try:
        task = await service.approve(
            owner_id=principal.subject,
            task_id=task_id,
            reviewed_action_digest=request.action_digest,
        )
    except TaskReviewMismatchError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (TaskStateError, TaskIntegrityError) as exc:
        # Expiry and digest failures intentionally transition the task to a terminal state.
        # Persist that audit-relevant state before the HTTP error causes dependency rollback.
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    await session.refresh(task)
    return TaskResponse.from_model(task)


@router.post("/{task_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(
    task_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    try:
        task = await TaskRepository(session).cancel(task_id=task_id, owner_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
