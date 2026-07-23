"""Read and digest owner-scoped personality lifecycle state."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.lifecycle_types import (
    PersonalityExportBundle,
    PersonalityLifecycleEvent,
    current_digest,
    feedback_export,
    lifecycle_event,
    revision_export,
    validate_owner_id,
    validate_task_id,
)
from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileLifecycleRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.adaptation.repository import PersonalityRepository
from mongars.db.models import TaskQueue

PERSONALITY_TASK_KINDS = (
    "personality.profile.apply",
    "personality.profile.reset",
    "personality.profile.delete",
)
T = TypeVar("T")

ADAPTATION_EVENT_TYPES = (
    "explicit_feedback_recorded",
    "personality_profile_proposed",
    "personality_profile_applied",
    "personality_profile_reset_requested",
    "personality_profile_reset",
    "personality_profile_delete_requested",
    "personality_profile_deleted",
)


async def load_lifecycle_history(
    session: AsyncSession,
    *,
    owner_id: str,
    limit: int,
) -> tuple[PersonalityLifecycleEvent, ...]:
    owner = validate_owner_id(owner_id)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("personality lifecycle history limit must be between 1 and 100")
    records = list(
        (
            await session.scalars(
                select(PersonalityProfileLifecycleRecord)
                .where(PersonalityProfileLifecycleRecord.owner_id == owner)
                .order_by(PersonalityProfileLifecycleRecord.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    return tuple(lifecycle_event(record) for record in records)


async def load_export_bundle(
    session: AsyncSession,
    *,
    owner_id: str,
) -> PersonalityExportBundle:
    owner = validate_owner_id(owner_id)
    profile = await PersonalityRepository(session).current_snapshot(owner_id=owner)
    revisions = list(
        (
            await session.scalars(
                select(PersonalityProfileRevisionRecord)
                .where(PersonalityProfileRevisionRecord.owner_id == owner)
                .order_by(PersonalityProfileRevisionRecord.revision.asc())
            )
        ).all()
    )
    lifecycle = list(
        (
            await session.scalars(
                select(PersonalityProfileLifecycleRecord)
                .where(PersonalityProfileLifecycleRecord.owner_id == owner)
                .order_by(
                    PersonalityProfileLifecycleRecord.created_at.asc(),
                    PersonalityProfileLifecycleRecord.id.asc(),
                )
            )
        ).all()
    )
    feedback = list(
        (
            await session.scalars(
                select(ExplicitFeedbackRecord)
                .where(ExplicitFeedbackRecord.owner_id == owner)
                .order_by(
                    ExplicitFeedbackRecord.created_at.asc(),
                    ExplicitFeedbackRecord.feedback_id,
                )
            )
        ).all()
    )
    return PersonalityExportBundle(
        exported_at=datetime.now(UTC),
        profile=profile,
        revisions=tuple(revision_export(record) for record in revisions),
        lifecycle_events=tuple(lifecycle_event(record) for record in lifecycle),
        feedback=tuple(feedback_export(record) for record in feedback),
    )


async def deletion_state_digest(
    session: AsyncSession,
    *,
    owner_id: str,
    exclude_task_id: UUID | None = None,
    lock_tasks: bool = False,
) -> str:
    owner = validate_owner_id(owner_id)
    if exclude_task_id is not None:
        validate_task_id(exclude_task_id)
    if not isinstance(lock_tasks, bool):
        raise TypeError("lock_tasks must be a boolean")
    profile = await PersonalityRepository(session).current_snapshot(owner_id=owner)
    feedback = await _records(
        session,
        select(ExplicitFeedbackRecord)
        .where(ExplicitFeedbackRecord.owner_id == owner)
        .order_by(ExplicitFeedbackRecord.feedback_id),
    )
    revisions = await _records(
        session,
        select(PersonalityProfileRevisionRecord)
        .where(PersonalityProfileRevisionRecord.owner_id == owner)
        .order_by(PersonalityProfileRevisionRecord.revision),
    )
    lifecycle = await _records(
        session,
        select(PersonalityProfileLifecycleRecord)
        .where(PersonalityProfileLifecycleRecord.owner_id == owner)
        .order_by(PersonalityProfileLifecycleRecord.id),
    )
    task_query = select(TaskQueue).where(
        TaskQueue.owner_id == owner,
        TaskQueue.kind.in_(PERSONALITY_TASK_KINDS),
    )
    if exclude_task_id is not None:
        task_query = task_query.where(TaskQueue.id != exclude_task_id)
    task_query = task_query.order_by(TaskQueue.id)
    if lock_tasks:
        task_query = task_query.with_for_update()
    tasks = await _records(session, task_query)
    canonical = json.dumps(
        {
            "profile": {"revision": profile.revision, "digest": current_digest(profile)},
            "feedback": [
                {
                    "id": str(item.feedback_id),
                    "digest": item.feedback_digest,
                    "kind": item.kind,
                    "applied_task_id": (
                        str(item.applied_task_id) if item.applied_task_id else None
                    ),
                    "applied_revision": item.applied_revision,
                }
                for item in feedback
            ],
            "revisions": [
                {
                    "revision": item.revision,
                    "profile_digest": item.profile_digest,
                    "feedback_digest": item.feedback_digest,
                    "proposal_digest": item.proposal_digest,
                    "task_id": str(item.task_id),
                }
                for item in revisions
            ],
            "lifecycle": [
                {
                    "operation": item.operation,
                    "expected_revision": item.expected_revision,
                    "expected_profile_digest": item.expected_profile_digest,
                    "target_revision": item.target_revision,
                    "target_profile_digest": item.target_profile_digest,
                    "data_state_digest": item.data_state_digest,
                    "task_id": str(item.task_id),
                }
                for item in lifecycle
            ],
            "tasks": [
                {
                    "id": str(item.id),
                    "kind": item.kind,
                    "status": item.status,
                    "action_digest": item.action_digest,
                    "trace_id": item.trace_id,
                    "payload": item.payload,
                }
                for item in tasks
            ],
        },
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def _records(
    session: AsyncSession,
    statement: Select[tuple[T]],
) -> list[T]:
    return list((await session.scalars(statement)).all())
