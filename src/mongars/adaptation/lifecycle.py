"""Approval-gated reset, export, and privacy deletion for personality data."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.lifecycle_state import (
    ADAPTATION_EVENT_TYPES,
    PERSONALITY_TASK_KINDS,
    deletion_state_digest,
    load_export_bundle,
    load_lifecycle_history,
)
from mongars.adaptation.lifecycle_types import (
    PersonalityExportBundle,
    PersonalityFeedbackExport,
    PersonalityLifecycleEvent,
    PersonalityLifecycleOperation,
    PersonalityProfileLifecycleConflict,
    PersonalityProfileLifecycleDataError,
    PersonalityRevisionExport,
    ProfileDeletionApplication,
    ProfileResetApplication,
    current_digest,
    lifecycle_event,
    require_expected_state,
    result_rowcount,
    validate_owner_id,
    validate_task_id,
)
from mongars.adaptation.mimicry import EMPTY_PROFILE_DIGEST
from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileLifecycleRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.adaptation.repository import PersonalityRepository
from mongars.db.models import EpisodicEvent, TaskQueue
from mongars.orchestrator._cognitive_validation import validate_sha256_digest
from mongars.orchestrator.personality import PersonalitySnapshot


class PersonalityLifecycleRepository:
    """Persist reviewed lifecycle actions without exposing private values in receipts."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def lock_owner(self, *, owner_id: str) -> None:
        await self._lock_owner(validate_owner_id(owner_id))

    async def lock_deletion_writes(self) -> None:
        """Block feedback and task writes while an approved deletion commits."""

        await self._session.execute(
            text("LOCK TABLE explicit_feedback IN SHARE ROW EXCLUSIVE MODE")
        )
        # EXCLUSIVE conflicts with SELECT FOR UPDATE's ROW SHARE lock. Waiting for active
        # claims before taking it avoids a table-lock/row-lock deadlock.
        await self._session.execute(text("LOCK TABLE task_queue IN EXCLUSIVE MODE"))

    async def lifecycle_history(
        self,
        *,
        owner_id: str,
        limit: int = 100,
    ) -> tuple[PersonalityLifecycleEvent, ...]:
        return await load_lifecycle_history(
            self._session,
            owner_id=owner_id,
            limit=limit,
        )

    async def export_bundle(self, *, owner_id: str) -> PersonalityExportBundle:
        owner = validate_owner_id(owner_id)
        await self._lock_owner(owner)
        await self._session.execute(text("LOCK TABLE explicit_feedback IN SHARE MODE"))
        return await load_export_bundle(self._session, owner_id=owner)

    async def deletion_state_digest(
        self,
        *,
        owner_id: str,
        exclude_task_id: UUID | None = None,
        lock_tasks: bool = False,
    ) -> str:
        return await deletion_state_digest(
            self._session,
            owner_id=owner_id,
            exclude_task_id=exclude_task_id,
            lock_tasks=lock_tasks,
        )

    async def reset_profile(
        self,
        *,
        owner_id: str,
        expected_revision: int,
        expected_profile_digest: str,
        target_revision: int,
        target_profile_digest: str,
        task_id: UUID,
    ) -> ProfileResetApplication:
        owner = validate_owner_id(owner_id)
        validate_task_id(task_id)
        await self._lock_owner(owner)

        existing = await self._lifecycle_for_task(owner_id=owner, task_id=task_id)
        if existing is not None:
            event = lifecycle_event(existing)
            expected = (
                "reset",
                expected_revision,
                expected_profile_digest,
                target_revision,
                target_profile_digest,
            )
            actual = (
                event.operation,
                event.expected_revision,
                event.expected_profile_digest,
                event.target_revision,
                event.target_profile_digest,
            )
            if actual != expected:
                raise PersonalityProfileLifecycleDataError(
                    "reset task does not match its immutable lifecycle receipt"
                )
            current = await PersonalityRepository(self._session).current_snapshot(
                owner_id=owner
            )
            if (
                current.revision != target_revision
                or current_digest(current) != target_profile_digest
                or current.source != "approved_profile"
                or current.preferences
            ):
                raise PersonalityProfileLifecycleDataError(
                    "reset lifecycle receipt does not match the current profile"
                )
            return ProfileResetApplication(snapshot=current, applied=False)

        current = await PersonalityRepository(self._session).current_snapshot(owner_id=owner)
        require_expected_state(
            current,
            expected_revision=expected_revision,
            expected_profile_digest=expected_profile_digest,
        )
        if not current.preferences:
            raise PersonalityProfileLifecycleConflict("personality profile is already reset")
        if target_revision != current.revision + 1:
            raise PersonalityProfileLifecycleConflict(
                "reset target revision is not current + 1"
            )
        if target_profile_digest != EMPTY_PROFILE_DIGEST:
            raise PersonalityProfileLifecycleConflict(
                "reset target digest is not the empty profile"
            )

        target = PersonalitySnapshot(
            revision=target_revision,
            source="approved_profile",
            preferences=(),
            profile_digest=target_profile_digest,
        )
        current_record = cast(
            PersonalityProfileRecord | None,
            await self._session.get(
                PersonalityProfileRecord,
                owner,
                with_for_update=True,
            ),
        )
        if current_record is None:
            raise PersonalityProfileLifecycleDataError(
                "reviewed reset references a missing current profile row"
            )
        current_record.revision = target.revision
        current_record.profile_digest = cast(str, target.profile_digest)
        current_record.source = target.source
        current_record.preferences = []
        current_record.updated_at = datetime.now(UTC)
        self._session.add(
            PersonalityProfileLifecycleRecord(
                owner_id=owner,
                operation="reset",
                expected_revision=expected_revision,
                expected_profile_digest=expected_profile_digest,
                target_revision=target_revision,
                target_profile_digest=target_profile_digest,
                data_state_digest=None,
                task_id=task_id,
            )
        )
        await self._session.flush()
        return ProfileResetApplication(snapshot=target, applied=True)

    async def delete_profile_data(
        self,
        *,
        owner_id: str,
        expected_revision: int,
        expected_profile_digest: str,
        expected_data_state_digest: str,
        task_id: UUID,
        trace_id: str,
    ) -> ProfileDeletionApplication:
        owner = validate_owner_id(owner_id)
        validate_task_id(task_id)
        reviewed_digest = validate_sha256_digest(
            expected_data_state_digest,
            field="personality deletion data_state_digest",
        )
        if not isinstance(trace_id, str) or not trace_id:
            raise ValueError("trace_id must be a non-empty string")
        await self._lock_owner(owner)
        await self.lock_deletion_writes()

        existing = await self._lifecycle_for_task(owner_id=owner, task_id=task_id)
        if existing is not None:
            event = lifecycle_event(existing)
            if (
                event.operation != "delete"
                or event.expected_revision != expected_revision
                or event.expected_profile_digest != expected_profile_digest
                or event.target_revision != 0
                or event.target_profile_digest != EMPTY_PROFILE_DIGEST
                or event.data_state_digest != reviewed_digest
            ):
                raise PersonalityProfileLifecycleDataError(
                    "delete task does not match its immutable lifecycle receipt"
                )
            current = await PersonalityRepository(self._session).current_snapshot(
                owner_id=owner
            )
            if current != PersonalitySnapshot.default():
                raise PersonalityProfileLifecycleDataError(
                    "delete lifecycle receipt exists while profile data remains"
                )
            return ProfileDeletionApplication(False, 0, 0, 0, 0)

        current = await PersonalityRepository(self._session).current_snapshot(owner_id=owner)
        require_expected_state(
            current,
            expected_revision=expected_revision,
            expected_profile_digest=expected_profile_digest,
        )
        live_digest = await self.deletion_state_digest(
            owner_id=owner,
            exclude_task_id=task_id,
            lock_tasks=True,
        )
        if live_digest != reviewed_digest:
            raise PersonalityProfileLifecycleConflict(
                "personality data changed after deletion review"
            )

        tasks = list(
            (
                await self._session.scalars(
                    select(TaskQueue).where(
                        TaskQueue.owner_id == owner,
                        TaskQueue.kind.in_(PERSONALITY_TASK_KINDS),
                        TaskQueue.id != task_id,
                    )
                )
            ).all()
        )
        event_scope = EpisodicEvent.event_type.in_(ADAPTATION_EVENT_TYPES)
        trace_ids = tuple(task.trace_id for task in tasks)
        if trace_ids:
            event_scope = or_(event_scope, EpisodicEvent.trace_id.in_(trace_ids))
        deleted_events = await self._session.execute(
            delete(EpisodicEvent).where(
                EpisodicEvent.owner_id == owner,
                event_scope,
                EpisodicEvent.trace_id != trace_id,
            )
        )
        await self._session.execute(
            delete(PersonalityProfileLifecycleRecord).where(
                PersonalityProfileLifecycleRecord.owner_id == owner
            )
        )
        deleted_revisions = await self._session.execute(
            delete(PersonalityProfileRevisionRecord).where(
                PersonalityProfileRevisionRecord.owner_id == owner
            )
        )
        await self._session.execute(
            delete(PersonalityProfileRecord).where(
                PersonalityProfileRecord.owner_id == owner
            )
        )
        deleted_feedback = await self._session.execute(
            delete(ExplicitFeedbackRecord).where(
                ExplicitFeedbackRecord.owner_id == owner
            )
        )
        deleted_tasks = await self._session.execute(
            delete(TaskQueue).where(
                TaskQueue.owner_id == owner,
                TaskQueue.kind.in_(PERSONALITY_TASK_KINDS),
                TaskQueue.id != task_id,
            )
        )
        self._session.add(
            PersonalityProfileLifecycleRecord(
                owner_id=owner,
                operation="delete",
                expected_revision=expected_revision,
                expected_profile_digest=expected_profile_digest,
                target_revision=0,
                target_profile_digest=EMPTY_PROFILE_DIGEST,
                data_state_digest=reviewed_digest,
                task_id=task_id,
            )
        )
        await self._session.flush()
        return ProfileDeletionApplication(
            applied=True,
            deleted_feedback=result_rowcount(deleted_feedback),
            deleted_revisions=result_rowcount(deleted_revisions),
            deleted_tasks=result_rowcount(deleted_tasks),
            deleted_events=result_rowcount(deleted_events),
        )

    async def _lock_owner(self, owner_id: str) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:owner_id, 0))"),
            {"owner_id": owner_id},
        )

    async def _lifecycle_for_task(
        self,
        *,
        owner_id: str,
        task_id: UUID,
    ) -> PersonalityProfileLifecycleRecord | None:
        statement = (
            select(PersonalityProfileLifecycleRecord)
            .where(
                PersonalityProfileLifecycleRecord.owner_id == owner_id,
                PersonalityProfileLifecycleRecord.task_id == task_id,
            )
            .with_for_update()
        )
        return cast(
            PersonalityProfileLifecycleRecord | None,
            await self._session.scalar(statement),
        )


__all__ = [
    "PersonalityExportBundle",
    "PersonalityFeedbackExport",
    "PersonalityLifecycleEvent",
    "PersonalityLifecycleOperation",
    "PersonalityLifecycleRepository",
    "PersonalityProfileLifecycleConflict",
    "PersonalityProfileLifecycleDataError",
    "PersonalityRevisionExport",
    "ProfileDeletionApplication",
    "ProfileResetApplication",
]
