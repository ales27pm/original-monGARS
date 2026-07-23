"""Owner-scoped persistence and optimistic application for explicit Mimétisme updates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.adaptation.mimicry import (
    EMPTY_PROFILE_DIGEST,
    ProfileDeltaProposal,
    personality_profile_digest,
    propose_profile_delta,
)
from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.orchestrator._cognitive_validation import validate_sha256_digest
from mongars.orchestrator.personality import (
    PersonalityDimension,
    PersonalityPreference,
    PersonalitySnapshot,
    PersonalitySource,
)


class FeedbackIdentityConflict(ValueError):
    """The same owner/feedback UUID was reused with different canonical content."""


class PersonalityProfileConflict(ValueError):
    """The reviewed proposal no longer matches the owner's current profile state."""


class PersonalityProfileDataError(RuntimeError):
    """Persisted profile data violates the immutable personality contract."""


@dataclass(frozen=True, slots=True)
class FeedbackReceipt:
    feedback_id: UUID
    feedback_digest: str
    kind: str
    created: bool
    applied_task_id: UUID | None
    applied_revision: int | None


@dataclass(frozen=True, slots=True)
class ProfileApplication:
    snapshot: PersonalitySnapshot
    applied: bool


@dataclass(frozen=True, slots=True)
class PersonalityRevision:
    snapshot: PersonalitySnapshot
    feedback_id: UUID
    feedback_digest: str
    proposal_digest: str
    task_id: UUID
    changed_dimension: PersonalityDimension
    conflict: bool
    created_at: datetime


class PersonalityRepository:
    """Persist explicit observations and atomically apply reviewed profile proposals."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_feedback(
        self,
        *,
        owner_id: str,
        feedback: ExplicitFeedback,
    ) -> FeedbackReceipt:
        owner = _validate_owner_id(owner_id)
        if not isinstance(
            feedback,
            (CorrectionFeedback, HelpfulnessFeedback, PreferenceFeedback),
        ):
            raise TypeError("feedback must be an explicit feedback contract")

        payload = feedback.as_dict()
        kind = cast(str, payload["kind"])
        response_trace_id = cast(str | None, payload.get("response_trace_id"))
        statement = (
            insert(ExplicitFeedbackRecord)
            .values(
                owner_id=owner,
                feedback_id=feedback.feedback_id,
                feedback_digest=feedback.feedback_digest,
                kind=kind,
                response_trace_id=response_trace_id,
                payload=payload,
            )
            .on_conflict_do_nothing(
                index_elements=["owner_id", "feedback_id"],
            )
            .returning(ExplicitFeedbackRecord.feedback_id)
        )
        inserted = await self._session.scalar(statement)
        if inserted is not None:
            return FeedbackReceipt(
                feedback_id=feedback.feedback_id,
                feedback_digest=feedback.feedback_digest,
                kind=kind,
                created=True,
                applied_task_id=None,
                applied_revision=None,
            )

        existing = await self._get_feedback(
            owner_id=owner,
            feedback_id=feedback.feedback_id,
            for_update=False,
        )
        if existing is None:
            raise RuntimeError("feedback insert conflict did not preserve an existing row")
        if (
            existing.feedback_digest != feedback.feedback_digest
            or existing.kind != kind
            or existing.response_trace_id != response_trace_id
            or existing.payload != payload
        ):
            raise FeedbackIdentityConflict(
                "feedback_id is already bound to different canonical content"
            )
        return _feedback_receipt(existing, created=False)

    async def current_snapshot(self, *, owner_id: str) -> PersonalitySnapshot:
        owner = _validate_owner_id(owner_id)
        record = cast(
            PersonalityProfileRecord | None,
            await self._session.get(PersonalityProfileRecord, owner),
        )
        return _snapshot_from_record(record)

    async def revision_history(
        self,
        *,
        owner_id: str,
        limit: int = 100,
    ) -> tuple[PersonalityRevision, ...]:
        owner = _validate_owner_id(owner_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("personality revision history limit must be between 1 and 100")
        statement = (
            select(PersonalityProfileRevisionRecord)
            .where(PersonalityProfileRevisionRecord.owner_id == owner)
            .order_by(PersonalityProfileRevisionRecord.revision.desc())
            .limit(limit)
        )
        records = list((await self._session.scalars(statement)).all())
        return tuple(_revision_from_record(record) for record in records)

    async def apply_proposal(
        self,
        *,
        owner_id: str,
        proposal: ProfileDeltaProposal,
        task_id: UUID,
    ) -> ProfileApplication:
        owner = _validate_owner_id(owner_id)
        if not isinstance(proposal, ProfileDeltaProposal):
            raise TypeError("proposal must be a ProfileDeltaProposal")
        if not isinstance(task_id, UUID):
            raise TypeError("task_id must be a UUID")

        await self._lock_owner(owner)
        feedback = await self._get_feedback(
            owner_id=owner,
            feedback_id=proposal.feedback_id,
            for_update=True,
        )
        if feedback is None:
            raise PersonalityProfileConflict("proposal feedback does not exist for this owner")
        if feedback.feedback_digest != proposal.feedback_digest:
            raise PersonalityProfileConflict("proposal feedback digest does not match persistence")
        if feedback.kind != "preference":
            raise PersonalityProfileConflict("only direct preference feedback can update a profile")
        if feedback.applied_revision is not None or feedback.applied_task_id is not None:
            return await self._resolve_applied_feedback(
                owner_id=owner,
                feedback=feedback,
                proposal=proposal,
                task_id=task_id,
            )

        current_record = cast(
            PersonalityProfileRecord | None,
            await self._session.get(
                PersonalityProfileRecord,
                owner,
                with_for_update=True,
            ),
        )
        current = _snapshot_from_record(current_record)
        current_digest = personality_profile_digest(current.preferences)
        if current.revision != proposal.expected_revision:
            raise PersonalityProfileConflict("personality revision changed after proposal review")
        if current_digest != proposal.expected_profile_digest:
            raise PersonalityProfileConflict("personality digest changed after proposal review")
        if current.source != "default" and current.profile_digest != current_digest:
            raise PersonalityProfileDataError(
                "current personality digest does not match its persisted preferences"
            )
        if current.source == "default" and current_digest != EMPTY_PROFILE_DIGEST:
            raise PersonalityProfileDataError("default personality digest is not empty")

        persisted_preference = _preference_feedback_from_record(feedback)
        canonical_proposal = propose_profile_delta(current, persisted_preference)
        if canonical_proposal is None or canonical_proposal != proposal:
            raise PersonalityProfileConflict(
                "profile proposal does not match the persisted explicit preference"
            )

        target = proposal.target_snapshot
        existing_revision = cast(
            PersonalityProfileRevisionRecord | None,
            await self._session.get(
                PersonalityProfileRevisionRecord,
                {"owner_id": owner, "revision": target.revision},
                with_for_update=True,
            ),
        )
        if existing_revision is not None:
            raise PersonalityProfileConflict("target personality revision already exists")

        now = datetime.now(UTC)
        preference_payloads = _preference_payloads(target.preferences)
        if current_record is None:
            current_record = PersonalityProfileRecord(
                owner_id=owner,
                revision=target.revision,
                profile_digest=cast(str, target.profile_digest),
                source=target.source,
                preferences=preference_payloads,
                created_at=now,
                updated_at=now,
            )
            self._session.add(current_record)
        else:
            current_record.revision = target.revision
            current_record.profile_digest = cast(str, target.profile_digest)
            current_record.source = target.source
            current_record.preferences = preference_payloads
            current_record.updated_at = now

        revision = PersonalityProfileRevisionRecord(
            owner_id=owner,
            revision=target.revision,
            profile_digest=cast(str, target.profile_digest),
            source=target.source,
            preferences=preference_payloads,
            feedback_id=proposal.feedback_id,
            feedback_digest=proposal.feedback_digest,
            proposal_digest=proposal.proposal_digest,
            task_id=task_id,
            changed_dimension=proposal.changed_dimension,
            conflict=proposal.conflict,
            created_at=now,
        )
        self._session.add(revision)
        feedback.applied_task_id = task_id
        feedback.applied_revision = target.revision
        await self._session.flush()
        return ProfileApplication(snapshot=target, applied=True)

    async def _resolve_applied_feedback(
        self,
        *,
        owner_id: str,
        feedback: ExplicitFeedbackRecord,
        proposal: ProfileDeltaProposal,
        task_id: UUID,
    ) -> ProfileApplication:
        if (
            feedback.applied_task_id != task_id
            or feedback.applied_revision != proposal.target_snapshot.revision
        ):
            raise PersonalityProfileConflict("feedback was already applied by another task")
        revision = cast(
            PersonalityProfileRevisionRecord | None,
            await self._session.get(
                PersonalityProfileRevisionRecord,
                {
                    "owner_id": owner_id,
                    "revision": proposal.target_snapshot.revision,
                },
                with_for_update=True,
            ),
        )
        if revision is None:
            raise PersonalityProfileDataError(
                "applied feedback references a missing personality revision"
            )
        stored = _revision_from_record(revision)
        if (
            stored.task_id != task_id
            or stored.feedback_digest != proposal.feedback_digest
            or stored.proposal_digest != proposal.proposal_digest
            or stored.snapshot != proposal.target_snapshot
        ):
            raise PersonalityProfileDataError(
                "applied feedback does not match its immutable revision history"
            )
        return ProfileApplication(snapshot=stored.snapshot, applied=False)

    async def _get_feedback(
        self,
        *,
        owner_id: str,
        feedback_id: UUID,
        for_update: bool,
    ) -> ExplicitFeedbackRecord | None:
        return cast(
            ExplicitFeedbackRecord | None,
            await self._session.get(
                ExplicitFeedbackRecord,
                {"owner_id": owner_id, "feedback_id": feedback_id},
                with_for_update=for_update,
            ),
        )

    async def _lock_owner(self, owner_id: str) -> None:
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:owner_id, 0))"),
            {"owner_id": owner_id},
        )


def _validate_owner_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("owner_id must be a non-empty canonical string")
    if len(value) > 255:
        raise ValueError("owner_id exceeds the persistence limit")
    return value


def _feedback_receipt(record: ExplicitFeedbackRecord, *, created: bool) -> FeedbackReceipt:
    return FeedbackReceipt(
        feedback_id=record.feedback_id,
        feedback_digest=record.feedback_digest,
        kind=record.kind,
        created=created,
        applied_task_id=record.applied_task_id,
        applied_revision=record.applied_revision,
    )


def _preference_feedback_from_record(
    record: ExplicitFeedbackRecord,
) -> PreferenceFeedback:
    payload = record.payload
    required = {"desired_value", "dimension", "feedback_id", "kind"}
    optional = {"response_trace_id"}
    if not isinstance(payload, dict) or not required <= set(payload):
        raise PersonalityProfileDataError("persisted preference feedback payload is incomplete")
    if set(payload) - required - optional:
        raise PersonalityProfileDataError("persisted preference feedback fields are invalid")
    if payload.get("kind") != "preference":
        raise PersonalityProfileDataError("persisted feedback kind does not match its payload")
    if payload.get("feedback_id") != str(record.feedback_id):
        raise PersonalityProfileDataError("persisted feedback UUID does not match its key")
    try:
        feedback = PreferenceFeedback(
            feedback_id=record.feedback_id,
            dimension=cast(PersonalityDimension, payload["dimension"]),
            desired_value=cast(float, payload["desired_value"]),
            response_trace_id=cast(str | None, payload.get("response_trace_id")),
        )
    except (TypeError, ValueError) as exc:
        raise PersonalityProfileDataError("persisted preference feedback is invalid") from exc
    if feedback.feedback_digest != record.feedback_digest:
        raise PersonalityProfileDataError(
            "persisted preference feedback digest does not match its payload"
        )
    return feedback


def _snapshot_from_record(record: PersonalityProfileRecord | None) -> PersonalitySnapshot:
    if record is None:
        return PersonalitySnapshot.default()
    return _snapshot_from_values(
        revision=record.revision,
        source=record.source,
        profile_digest=record.profile_digest,
        preferences=record.preferences,
    )


def _snapshot_from_values(
    *,
    revision: object,
    source: object,
    profile_digest: object,
    preferences: object,
) -> PersonalitySnapshot:
    if not isinstance(preferences, list):
        raise PersonalityProfileDataError("persisted personality preferences are not a list")
    parsed = tuple(_preference_from_payload(item) for item in preferences)
    try:
        snapshot = PersonalitySnapshot(
            revision=cast(int, revision),
            source=cast(PersonalitySource, source),
            preferences=parsed,
            profile_digest=cast(str, profile_digest),
        )
    except (TypeError, ValueError) as exc:
        raise PersonalityProfileDataError("persisted personality snapshot is invalid") from exc
    if snapshot.profile_digest != personality_profile_digest(snapshot.preferences):
        raise PersonalityProfileDataError(
            "persisted personality digest does not match its preferences"
        )
    return snapshot


def _preference_from_payload(value: object) -> PersonalityPreference:
    if not isinstance(value, dict):
        raise PersonalityProfileDataError("persisted personality preference is not an object")
    expected_keys = {"confidence", "dimension", "evidence_count", "value"}
    if set(value) != expected_keys:
        raise PersonalityProfileDataError("persisted personality preference fields are invalid")
    try:
        return PersonalityPreference(
            dimension=cast(PersonalityDimension, value["dimension"]),
            value=cast(float, value["value"]),
            confidence=cast(float, value["confidence"]),
            evidence_count=cast(int, value["evidence_count"]),
        )
    except (TypeError, ValueError) as exc:
        raise PersonalityProfileDataError("persisted personality preference is invalid") from exc


def _preference_payloads(
    preferences: Sequence[PersonalityPreference],
) -> list[dict[str, Any]]:
    return [cast(dict[str, Any], preference.as_dict()) for preference in preferences]


def _revision_from_record(record: PersonalityProfileRevisionRecord) -> PersonalityRevision:
    try:
        validate_sha256_digest(
            record.feedback_digest,
            field="persisted personality revision feedback_digest",
        )
        validate_sha256_digest(
            record.proposal_digest,
            field="persisted personality revision proposal_digest",
        )
    except ValueError as exc:
        raise PersonalityProfileDataError("persisted revision digests are invalid") from exc
    if not isinstance(record.feedback_id, UUID) or not isinstance(record.task_id, UUID):
        raise PersonalityProfileDataError("persisted revision identifiers are invalid")
    if not isinstance(record.conflict, bool):
        raise PersonalityProfileDataError("persisted revision conflict flag is invalid")
    snapshot = _snapshot_from_values(
        revision=record.revision,
        source=record.source,
        profile_digest=record.profile_digest,
        preferences=record.preferences,
    )
    try:
        changed_dimension = cast(PersonalityDimension, record.changed_dimension)
        if changed_dimension not in {
            preference.dimension for preference in snapshot.preferences
        }:
            raise ValueError("changed dimension is missing from the revision snapshot")
    except ValueError as exc:
        raise PersonalityProfileDataError("persisted revision metadata is invalid") from exc
    return PersonalityRevision(
        snapshot=snapshot,
        feedback_id=record.feedback_id,
        feedback_digest=record.feedback_digest,
        proposal_digest=record.proposal_digest,
        task_id=record.task_id,
        changed_dimension=changed_dimension,
        conflict=record.conflict,
        created_at=record.created_at,
    )


__all__ = [
    "FeedbackIdentityConflict",
    "FeedbackReceipt",
    "PersonalityProfileConflict",
    "PersonalityProfileDataError",
    "PersonalityRepository",
    "PersonalityRevision",
    "ProfileApplication",
]
