from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.adaptation.mimicry import (
    ProfileDeltaProposal,
    profile_delta_proposal_from_payload,
    propose_profile_delta,
)
from mongars.adaptation.repository import (
    FeedbackIdentityConflict,
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.api.dependencies import (
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import (
    CorrectionFeedbackRequest,
    ExplicitFeedbackRequest,
    FeedbackSubmissionResponse,
    HelpfulnessFeedbackRequest,
    PersonalityProfileResponse,
    PersonalityRevisionResponse,
    PreferenceFeedbackRequest,
    TaskResponse,
)
from mongars.db.models import TaskQueue
from mongars.events.repository import EventRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskService

router = APIRouter(prefix="/v1/adaptation", tags=["adaptation"])


@router.post(
    "/feedback",
    response_model=FeedbackSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_feedback(
    request: ExplicitFeedbackRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
) -> FeedbackSubmissionResponse:
    try:
        feedback = _feedback_from_request(request)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    repository = PersonalityRepository(session)
    try:
        receipt = await repository.record_feedback(
            owner_id=principal.subject,
            feedback=feedback,
        )
    except FeedbackIdentityConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    trace_id = feedback.response_trace_id or f"fb_{feedback.feedback_id.hex}"
    if receipt.created:
        await EventRepository(session).record(
            owner_id=principal.subject,
            trace_id=trace_id,
            actor="user",
            event_type="explicit_feedback_recorded",
            summary=f"Recorded explicit {receipt.kind} feedback",
            payload={
                "feedback_id": str(receipt.feedback_id),
                "feedback_digest": receipt.feedback_digest,
                "kind": receipt.kind,
                "response_trace_id": feedback.response_trace_id,
            },
        )

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
            trace_id=trace_id,
        )

    try:
        profile = await repository.current_snapshot(owner_id=principal.subject)
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc

    return FeedbackSubmissionResponse(
        feedback_id=receipt.feedback_id,
        feedback_digest=receipt.feedback_digest,
        kind=cast(str, receipt.kind),
        created=receipt.created,
        profile=PersonalityProfileResponse.from_snapshot(profile),
        proposal_task=(
            TaskResponse.from_model(proposal_task) if proposal_task is not None else None
        ),
    )


@router.get("/profile", response_model=PersonalityProfileResponse)
async def get_profile(
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PersonalityProfileResponse:
    try:
        snapshot = await PersonalityRepository(session).current_snapshot(
            owner_id=principal.subject
        )
    except PersonalityProfileDataError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persisted personality profile is invalid",
        ) from exc
    return PersonalityProfileResponse.from_snapshot(snapshot)


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
    return [PersonalityRevisionResponse.from_revision(item) for item in revisions]


async def _preference_task(
    *,
    owner_id: str,
    feedback: PreferenceFeedback,
    receipt_task_id: object,
    repository: PersonalityRepository,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
    trace_id: str,
) -> TaskQueue:
    task_repository = TaskRepository(session)
    if receipt_task_id is not None:
        task = await task_repository.get_for_owner(
            task_id=cast(object, receipt_task_id),
            owner_id=owner_id,
        )
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="applied feedback references a missing task",
            )
        _verify_feedback_task(task, feedback)
        return task

    dedupe_key = f"personality.profile.apply:{feedback.feedback_id}"
    existing = await _task_by_dedupe_key(
        session=session,
        owner_id=owner_id,
        dedupe_key=dedupe_key,
    )
    if existing is not None:
        _verify_feedback_task(existing, feedback)
        return existing

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

    service = TaskService(
        settings=settings,
        repository=task_repository,
        events=EventRepository(session),
        policy=policy,
    )
    created = False
    try:
        async with session.begin_nested():
            task = await service.create(
                owner_id=owner_id,
                kind="personality.profile.apply",
                payload=proposal.as_task_payload(),
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
                detail="duplicate personality proposal could not be resolved",
            )
        _verify_feedback_task(task, feedback)

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
    session: SessionDependency,
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
    try:
        proposal = profile_delta_proposal_from_payload(task.payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing feedback task payload is invalid",
        ) from exc
    if (
        proposal.feedback_id != feedback.feedback_id
        or proposal.feedback_digest != feedback.feedback_digest
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="existing feedback task does not match this feedback",
        )


def _feedback_from_request(request: ExplicitFeedbackRequest) -> ExplicitFeedback:
    if isinstance(request, HelpfulnessFeedbackRequest):
        return HelpfulnessFeedback(
            feedback_id=request.feedback_id,
            response_trace_id=request.response_trace_id,
            helpful=request.helpful,
        )
    if isinstance(request, CorrectionFeedbackRequest):
        return CorrectionFeedback(
            feedback_id=request.feedback_id,
            response_trace_id=request.response_trace_id,
            correction_text=request.correction_text,
        )
    if isinstance(request, PreferenceFeedbackRequest):
        return PreferenceFeedback(
            feedback_id=request.feedback_id,
            dimension=request.dimension,
            desired_value=request.desired_value,
            response_trace_id=request.response_trace_id,
        )
    raise TypeError("unsupported explicit feedback request")
