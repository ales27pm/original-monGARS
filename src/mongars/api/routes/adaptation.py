from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    FeedbackKind,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.adaptation.mimicry import (
    ProfileDeltaProposal,
    propose_profile_delta,
)
from mongars.adaptation.repository import (
    FeedbackIdentityConflict,
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.adaptation.typed_feedback import (
    ResolvedResponseTarget,
    ResponseTraceIntegrityError,
    ResponseTraceNotFound,
    record_typed_feedback_event,
    resolve_owned_response_target,
)
from mongars.api.dependencies import (
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import (
    ExplicitFeedbackCreateCorrectionRequest,
    ExplicitFeedbackCreateHelpfulnessRequest,
    ExplicitFeedbackCreatePreferenceRequest,
    ExplicitFeedbackCreateRequest,
    ExplicitFeedbackCreateResponse,
    PersonalityExportResponse,
    PersonalityHistoryResponse,
    PersonalityRevisionResponse,
    PersonalitySnapshotResponse,
    ProfileApplyFromFeedbackRequest,
    TaskResponse,
)
from mongars.config import Settings
from mongars.db.models import TaskQueue
from mongars.events.repository import EventRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskIntegrityError, TaskService
from mongars.security.policy import ToolPolicy

router = APIRouter(prefix="/v1/adaptation", tags=["adaptation"])


def _to_feedback_payload(
    request: ExplicitFeedbackCreateRequest,
) -> tuple[ExplicitFeedback, str]:
    if isinstance(request, ExplicitFeedbackCreateCorrectionRequest):
        return (
            CorrectionFeedback(
                feedback_id=request.feedback_id,
                response_trace_id=request.response_trace_id,
                correction_text=request.correction_text,
            ),
            "correction",
        )
    if isinstance(request, ExplicitFeedbackCreateHelpfulnessRequest):
        return (
            HelpfulnessFeedback(
                feedback_id=request.feedback_id,
                response_trace_id=request.response_trace_id,
                helpful=request.helpful,
            ),
            "helpfulness",
        )
    if isinstance(request, ExplicitFeedbackCreatePreferenceRequest):
        return (
            PreferenceFeedback(
                feedback_id=request.feedback_id,
                dimension=request.dimension,
                desired_value=request.desired_value,
                response_trace_id=request.response_trace_id,
            ),
            "preference",
        )
    raise RuntimeError("unsupported explicit feedback request payload")


def _proposal_payload(proposal: ProfileDeltaProposal) -> dict[str, object]:
    payload = proposal.as_task_payload()
    if payload.get("feedback_id") is None:
        raise RuntimeError("proposal payload is missing feedback_id")
    payload["feedback_id"] = str(payload["feedback_id"])
    return payload


def _revision_to_response(item) -> PersonalityRevisionResponse:
    snapshot = PersonalitySnapshotResponse.from_model(item.snapshot)
    return PersonalityRevisionResponse(
        feedback_id=item.feedback_id,
        feedback_digest=item.feedback_digest,
        proposal_digest=item.proposal_digest,
        task_id=item.task_id,
        changed_dimension=item.changed_dimension,
        conflict=item.conflict,
        created_at=item.created_at,
        snapshot=snapshot,
    )


@router.post(
    "/feedback",
    response_model=ExplicitFeedbackCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_feedback(
    request: ExplicitFeedbackCreateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> ExplicitFeedbackCreateResponse:
    repository = PersonalityRepository(session)
    feedback, _kind = _to_feedback_payload(request)
    response_target: ResolvedResponseTarget | None = None
    if feedback.response_trace_id is not None:
        try:
            response_target = await resolve_owned_response_target(
                session=session,
                owner_id=principal.subject,
                trace_id=feedback.response_trace_id,
            )
        except ResponseTraceNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="response trace not found",
            ) from exc
        except ResponseTraceIntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="response trace identity is inconsistent",
            ) from exc

    try:
        receipt = await repository.record_feedback(
            owner_id=principal.subject,
            feedback=feedback,
        )
    except FeedbackIdentityConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    proposal_task: TaskQueue | None = None
    if isinstance(feedback, PreferenceFeedback):
        proposal_task = await _preference_task(
            owner_id=principal.subject,
            feedback=feedback,
            receipt_task_id=receipt.applied_task_id,
            repository=repository,
            session=session,
            settings=settings,
            policy=policy,
            trace_id=feedback.response_trace_id or f"fb_{feedback.feedback_id.hex}",
        )

    if receipt.created and response_target is not None:
        await record_typed_feedback_event(
            session=session,
            owner_id=principal.subject,
            target=response_target,
            feedback=feedback,
        )

    try:
        profile = await repository.current_snapshot(owner_id=principal.subject)
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc

    proposal_payload: dict[str, object] | None = None
    if isinstance(feedback, PreferenceFeedback):
        proposal = propose_profile_delta(profile, feedback)
        if proposal is not None:
            proposal_payload = _proposal_payload(proposal)

    return ExplicitFeedbackCreateResponse(
        feedback_id=receipt.feedback_id,
        kind=cast(FeedbackKind, receipt.kind),
        feedback_digest=receipt.feedback_digest,
        created=receipt.created,
        applied_task_id=receipt.applied_task_id,
        applied_revision=receipt.applied_revision,
        proposal=proposal_payload,
        profile=PersonalitySnapshotResponse.from_model(profile),
        proposal_task=(
            TaskResponse.from_model(proposal_task) if proposal_task is not None else None
        ),
    )


@router.get("/profile", response_model=PersonalitySnapshotResponse)
async def get_profile(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PersonalitySnapshotResponse:
    try:
        snapshot = await PersonalityRepository(session).current_snapshot(
            owner_id=principal.subject
        )
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc
    return PersonalitySnapshotResponse.from_model(snapshot)


@router.get("/profile/revisions", response_model=list[PersonalityRevisionResponse])
async def get_profile_revisions(
    principal: PrincipalDependency,
    session: SessionDependency,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PersonalityRevisionResponse]:
    try:
        revisions = await PersonalityRepository(session).revision_history(
            owner_id=principal.subject,
            limit=limit,
        )
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality revision history is invalid",
        ) from exc
    return [_revision_to_response(item) for item in revisions]


@router.get("/personality/current", response_model=PersonalitySnapshotResponse)
async def get_current_personality(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PersonalitySnapshotResponse:
    snapshot = await PersonalityRepository(session).current_snapshot(owner_id=principal.subject)
    return PersonalitySnapshotResponse.from_model(snapshot)


@router.get("/personality/revisions", response_model=list[PersonalityRevisionResponse])
async def list_personality_revisions(
    principal: PrincipalDependency,
    session: SessionDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[PersonalityRevisionResponse]:
    history = await PersonalityRepository(session).revision_history(
        owner_id=principal.subject,
        limit=limit,
    )
    return [_revision_to_response(item) for item in history]


@router.get("/personality/history", response_model=PersonalityHistoryResponse)
async def list_personality_history(
    principal: PrincipalDependency,
    session: SessionDependency,
    limit: int = Query(default=50, ge=1, le=100),
) -> PersonalityHistoryResponse:
    history = await PersonalityRepository(session).revision_history(
        owner_id=principal.subject,
        limit=limit,
    )
    return PersonalityHistoryResponse(items=tuple(_revision_to_response(item) for item in history))


@router.get("/personality/export", response_model=PersonalityExportResponse)
async def export_personality(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PersonalityExportResponse:
    repository = PersonalityRepository(session)
    current, history = await repository.export_profile(owner_id=principal.subject)
    return PersonalityExportResponse(
        exported_at=datetime.now(UTC),
        current=PersonalitySnapshotResponse.from_model(current),
        history=tuple(_revision_to_response(item) for item in history),
    )


@router.post("/personality/reset", response_model=PersonalitySnapshotResponse)
async def reset_personality(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PersonalitySnapshotResponse:
    snapshot = await PersonalityRepository(session).reset_profile(owner_id=principal.subject)
    return PersonalitySnapshotResponse.from_model(snapshot)


@router.delete("/personality", status_code=status.HTTP_204_NO_CONTENT)
async def delete_personality(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    await PersonalityRepository(session).delete_profile(owner_id=principal.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/personality/apply", response_model=TaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_profile_apply_task(
    request: ProfileApplyFromFeedbackRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> TaskResponse:
    repository = PersonalityRepository(session)
    feedback = await repository.feedback(owner_id=principal.subject, feedback_id=request.feedback_id)
    if feedback is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="feedback not found")
    if not isinstance(feedback, PreferenceFeedback):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="personality profile apply requires preference feedback",
        )
    current = await repository.current_snapshot(owner_id=principal.subject)
    try:
        proposal = propose_profile_delta(current, feedback)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="feedback did not produce a reviewable profile proposal",
        )

    service = TaskService(
        settings=settings,
        repository=TaskRepository(session),
        events=EventRepository(session),
        policy=policy,
    )
    try:
        task = await service.create(
            owner_id=principal.subject,
            kind="personality.profile.apply",
            payload=proposal.as_task_payload(),
        )
    except TaskIntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return TaskResponse.from_model(task)


async def _preference_task(
    *,
    owner_id: str,
    feedback: PreferenceFeedback,
    receipt_task_id: UUID | None,
    repository: PersonalityRepository,
    session: AsyncSession,
    settings: Settings,
    policy: ToolPolicy,
    trace_id: str,
) -> TaskQueue | None:
    task_repository = TaskRepository(session)
    dedupe_key = f"personality.profile.apply:{feedback.feedback_id}"
    existing = await _existing_preference_task(
        task_repository=task_repository,
        session=session,
        owner_id=owner_id,
        feedback=feedback,
        receipt_task_id=receipt_task_id,
        dedupe_key=dedupe_key,
    )
    if existing is not None:
        return existing

    proposal = await _preference_proposal(
        repository=repository,
        owner_id=owner_id,
        feedback=feedback,
    )
    return await _create_preference_task(
        task_repository=task_repository,
        session=session,
        settings=settings,
        policy=policy,
        owner_id=owner_id,
        feedback=feedback,
        proposal=proposal,
        dedupe_key=dedupe_key,
        trace_id=trace_id,
    )


async def _existing_preference_task(
    *,
    task_repository: TaskRepository,
    session: AsyncSession,
    owner_id: str,
    feedback: PreferenceFeedback,
    receipt_task_id: UUID | None,
    dedupe_key: str,
) -> TaskQueue | None:
    if receipt_task_id is not None:
        task = await task_repository.get_for_owner(
            task_id=receipt_task_id,
            owner_id=owner_id,
        )
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="applied feedback references a missing task",
            )
        _verify_feedback_task(task, feedback)
        return task

    task = await _task_by_dedupe_key(
        session=session,
        owner_id=owner_id,
        dedupe_key=dedupe_key,
    )
    if task is not None:
        _verify_feedback_task(task, feedback)
    return task


async def _preference_proposal(
    *,
    repository: PersonalityRepository,
    owner_id: str,
    feedback: PreferenceFeedback,
) -> ProfileDeltaProposal:
    try:
        current = await repository.current_snapshot(owner_id=owner_id)
        proposal = propose_profile_delta(current, feedback)
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc
    if proposal is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="direct preference feedback did not produce a proposal",
        )
    return proposal


async def _create_preference_task(
    *,
    task_repository: TaskRepository,
    session: AsyncSession,
    settings: Settings,
    policy: ToolPolicy,
    owner_id: str,
    feedback: PreferenceFeedback,
    proposal: ProfileDeltaProposal,
    dedupe_key: str,
    trace_id: str,
) -> TaskQueue:
    service = TaskService(
        settings=settings,
        repository=task_repository,
        events=EventRepository(session),
        policy=policy,
    )
    task: TaskQueue
    created = False
    try:
        async with session.begin_nested():
            task = await service.create(
                owner_id=owner_id,
                kind="personality.profile.apply",
                payload=proposal.as_task_payload(),
                priority=250,
                max_attempts=3,
                dedupe_key=dedupe_key,
            )
            created = True
    except IntegrityError:
        existing = await _task_by_dedupe_key(
            session=session,
            owner_id=owner_id,
            dedupe_key=dedupe_key,
        )
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="duplicate personality proposal could not be resolved",
            )
        _verify_feedback_task(existing, feedback)
        task = existing

    if created:
        await EventRepository(session).record(
            owner_id=owner_id,
            trace_id=trace_id,
            actor="cortex",
            event_type="personality_profile_proposed",
            summary="Proposed an explicit personality preference update",
            payload={
                "task_id": str(task.id),
                "feedback_id": str(feedback.feedback_id),
                "expected_revision": proposal.expected_revision,
                "target_revision": proposal.target_snapshot.revision,
                "changed_dimension": proposal.changed_dimension,
                "conflict": proposal.conflict,
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


def _verify_feedback_task(task: TaskQueue, feedback: PreferenceFeedback) -> None:
    if task.kind != "personality.profile.apply" or task.risk_level != "local_mutation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing feedback task has an incompatible action",
        )
    # Legacy compatibility path: tasks are verified by payload only.
    payload = cast(dict[str, object], task.payload)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing feedback task payload is invalid",
        )
    if payload.get("feedback_id") != feedback.feedback_id.hex:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing feedback task does not match this feedback",
        )


__all__ = ["router"]
