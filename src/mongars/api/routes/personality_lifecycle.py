"""Authenticated export and approval-gated personality lifecycle routes."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.lifecycle import (
    PersonalityLifecycleRepository,
    PersonalityProfileLifecycleDataError,
)
from mongars.adaptation.mimicry import EMPTY_PROFILE_DIGEST, personality_profile_digest
from mongars.adaptation.repository import PersonalityProfileDataError, PersonalityRepository
from mongars.api.adaptation_lifecycle_schemas import (
    PersonalityLifecycleEventResponse,
    PersonalityProfileExportResponse,
)
from mongars.api.dependencies import (
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import TaskResponse
from mongars.config import Settings
from mongars.db.models import TaskQueue
from mongars.events.repository import EventRepository
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.rm.contracts import normalize_task_payload
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskService
from mongars.security.policy import ToolPolicy

router = APIRouter(prefix="/v1/adaptation/profile", tags=["adaptation"])


@router.get("/export", response_model=PersonalityProfileExportResponse)
async def export_profile(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JSONResponse:
    try:
        bundle = await PersonalityLifecycleRepository(session).export_bundle(
            owner_id=principal.subject
        )
        response = PersonalityProfileExportResponse.from_bundle(bundle)
    except (PersonalityProfileDataError, PersonalityProfileLifecycleDataError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality export data is invalid",
        ) from exc

    stamp = bundle.exported_at.strftime("%Y%m%dT%H%M%SZ")
    filename = f"mongars-personality-r{bundle.profile.revision}-{stamp}.json"
    return JSONResponse(
        content=jsonable_encoder(response),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/lifecycle", response_model=list[PersonalityLifecycleEventResponse])
async def get_lifecycle_history(
    principal: PrincipalDependency,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PersonalityLifecycleEventResponse]:
    try:
        events = await PersonalityLifecycleRepository(session).lifecycle_history(
            owner_id=principal.subject,
            limit=limit,
        )
    except PersonalityProfileLifecycleDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality lifecycle history is invalid",
        ) from exc
    return [PersonalityLifecycleEventResponse.from_event(item) for item in events]


@router.post(
    "/reset",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_profile_reset(
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    lifecycle_repository = PersonalityLifecycleRepository(session)
    await lifecycle_repository.lock_owner(owner_id=principal.subject)
    current, current_digest = await _current_state(
        owner_id=principal.subject,
        session=session,
    )
    if not current.preferences:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="personality profile is already reset",
        )
    if current.revision >= 2_147_483_647:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="personality revision cannot be incremented",
        )
    payload: dict[str, Any] = {
        "expected_profile_digest": current_digest,
        "expected_revision": current.revision,
        "target_profile_digest": EMPTY_PROFILE_DIGEST,
        "target_revision": current.revision + 1,
    }
    task = await _lifecycle_task(
        owner_id=principal.subject,
        kind="personality.profile.reset",
        payload=payload,
        dedupe_key=f"personality.profile.reset:{current.revision}:{current_digest}",
        event_type="personality_profile_reset_requested",
        event_summary="Requested an approved personality profile reset",
        session=session,
        settings=settings,
        policy=policy,
    )
    return TaskResponse.from_model(task)


@router.post(
    "/delete",
    response_model=TaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_profile_delete(
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    lifecycle_repository = PersonalityLifecycleRepository(session)
    await lifecycle_repository.lock_owner(owner_id=principal.subject)
    current, current_digest = await _current_state(
        owner_id=principal.subject,
        session=session,
    )
    data_state_digest = await lifecycle_repository.deletion_state_digest(
        owner_id=principal.subject
    )
    payload: dict[str, Any] = {
        "data_state_digest": data_state_digest,
        "delete_feedback": True,
        "delete_history": True,
        "delete_tasks": True,
        "expected_profile_digest": current_digest,
        "expected_revision": current.revision,
    }
    task = await _lifecycle_task(
        owner_id=principal.subject,
        kind="personality.profile.delete",
        payload=payload,
        dedupe_key=f"personality.profile.delete:{data_state_digest}",
        event_type="personality_profile_delete_requested",
        event_summary="Requested approved deletion of personality profile data",
        session=session,
        settings=settings,
        policy=policy,
    )
    return TaskResponse.from_model(task)


async def _current_state(
    *,
    owner_id: str,
    session: AsyncSession,
) -> tuple[PersonalitySnapshot, str]:
    try:
        current = await PersonalityRepository(session).current_snapshot(owner_id=owner_id)
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc
    digest = personality_profile_digest(current.preferences)
    if current.source != "default" and current.profile_digest != digest:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile digest is invalid",
        )
    return current, digest


async def _lifecycle_task(
    *,
    owner_id: str,
    kind: str,
    payload: dict[str, Any],
    dedupe_key: str,
    event_type: str,
    event_summary: str,
    session: AsyncSession,
    settings: Settings,
    policy: ToolPolicy,
) -> TaskQueue:
    normalized = normalize_task_payload(kind, payload)
    existing = await _task_by_dedupe_key(
        session=session,
        owner_id=owner_id,
        dedupe_key=dedupe_key,
    )
    if existing is not None:
        _verify_lifecycle_task(existing, kind=kind, payload=normalized)
        return existing

    repository = TaskRepository(session)
    service = TaskService(
        settings=settings,
        repository=repository,
        events=EventRepository(session),
        policy=policy,
    )
    created = False
    try:
        async with session.begin_nested():
            task = await service.create(
                owner_id=owner_id,
                kind=kind,
                payload=normalized,
                priority=300,
                max_attempts=3,
                dedupe_key=dedupe_key,
            )
            created = True
    except IntegrityError:
        task = await _task_by_dedupe_key(
            session=session,
            owner_id=owner_id,
            dedupe_key=dedupe_key,
        )
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="duplicate personality lifecycle task could not be resolved",
            )
        _verify_lifecycle_task(task, kind=kind, payload=normalized)

    if created:
        await EventRepository(session).record(
            owner_id=owner_id,
            trace_id=task.trace_id,
            actor="user",
            event_type=event_type,
            summary=event_summary,
            payload={
                "task_id": str(task.id),
                "expected_revision": normalized["expected_revision"],
                "expected_profile_digest": normalized["expected_profile_digest"],
            },
        )
    return task


async def _task_by_dedupe_key(
    *,
    session: AsyncSession,
    owner_id: str,
    dedupe_key: str,
) -> TaskQueue | None:
    statement = select(TaskQueue).where(
        TaskQueue.owner_id == owner_id,
        TaskQueue.dedupe_key == dedupe_key,
    )
    return cast(TaskQueue | None, await session.scalar(statement))


def _verify_lifecycle_task(
    task: TaskQueue,
    *,
    kind: str,
    payload: dict[str, Any],
) -> None:
    if task.kind != kind or task.risk_level != "local_mutation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing lifecycle task has an incompatible action",
        )
    try:
        normalized = normalize_task_payload(task.kind, task.payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing lifecycle task payload is invalid",
        ) from exc
    if normalized != payload:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing lifecycle task does not match current profile state",
        )


__all__ = ["router"]
