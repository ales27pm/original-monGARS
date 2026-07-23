"""Contracts and validation helpers for reviewed personality lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from mongars.adaptation.mimicry import EMPTY_PROFILE_DIGEST, personality_profile_digest
from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileLifecycleRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.adaptation.repository import PersonalityProfileDataError
from mongars.orchestrator._cognitive_validation import validate_sha256_digest
from mongars.orchestrator.personality import (
    PERSONALITY_DIMENSIONS,
    PersonalityDimension,
    PersonalityPreference,
    PersonalitySnapshot,
    PersonalitySource,
)

type PersonalityLifecycleOperation = Literal["reset", "delete"]


class PersonalityProfileLifecycleConflict(ValueError):
    """The reviewed lifecycle action no longer matches current owner state."""


class PersonalityProfileLifecycleDataError(RuntimeError):
    """Persisted lifecycle data violates the reviewed personality contract."""


@dataclass(frozen=True, slots=True)
class PersonalityFeedbackExport:
    feedback_id: UUID
    feedback_digest: str
    kind: str
    response_trace_id: str | None
    payload: dict[str, Any]
    applied_task_id: UUID | None
    applied_revision: int | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityRevisionExport:
    snapshot: PersonalitySnapshot
    feedback_id: UUID
    feedback_digest: str
    proposal_digest: str
    task_id: UUID
    changed_dimension: PersonalityDimension
    conflict: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityLifecycleEvent:
    operation: PersonalityLifecycleOperation
    expected_revision: int
    expected_profile_digest: str
    target_revision: int
    target_profile_digest: str
    data_state_digest: str | None
    task_id: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PersonalityExportBundle:
    exported_at: datetime
    profile: PersonalitySnapshot
    revisions: tuple[PersonalityRevisionExport, ...]
    lifecycle_events: tuple[PersonalityLifecycleEvent, ...]
    feedback: tuple[PersonalityFeedbackExport, ...]


@dataclass(frozen=True, slots=True)
class ProfileResetApplication:
    snapshot: PersonalitySnapshot
    applied: bool


@dataclass(frozen=True, slots=True)
class ProfileDeletionApplication:
    applied: bool
    deleted_feedback: int
    deleted_revisions: int
    deleted_tasks: int
    deleted_events: int


def validate_owner_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("owner_id must be a non-empty canonical string")
    if len(value) > 255:
        raise ValueError("owner_id exceeds the persistence limit")
    return value


def validate_task_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("task_id must be a UUID")
    return value


def result_rowcount(result: object) -> int:
    value = getattr(result, "rowcount", None)
    return value if isinstance(value, int) and value > 0 else 0


def current_digest(snapshot: PersonalitySnapshot) -> str:
    digest = personality_profile_digest(snapshot.preferences)
    if snapshot.source == "default":
        if digest != EMPTY_PROFILE_DIGEST or snapshot.profile_digest is not None:
            raise PersonalityProfileDataError("default personality state is inconsistent")
        return digest
    if snapshot.profile_digest != digest:
        raise PersonalityProfileDataError(
            "current personality digest does not match its preferences"
        )
    return digest


def require_expected_state(
    current: PersonalitySnapshot,
    *,
    expected_revision: int,
    expected_profile_digest: str,
) -> None:
    if (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        raise ValueError("expected_revision must be a nonnegative integer")
    expected_digest = validate_sha256_digest(
        expected_profile_digest,
        field="personality lifecycle expected_profile_digest",
    )
    if current.revision != expected_revision:
        raise PersonalityProfileLifecycleConflict(
            "personality revision changed after lifecycle action review"
        )
    if current_digest(current) != expected_digest:
        raise PersonalityProfileLifecycleConflict(
            "personality digest changed after lifecycle action review"
        )


def feedback_export(record: ExplicitFeedbackRecord) -> PersonalityFeedbackExport:
    validate_sha256_digest(
        record.feedback_digest,
        field="exported personality feedback_digest",
    )
    if not isinstance(record.payload, dict):
        raise PersonalityProfileLifecycleDataError("exported feedback payload is invalid")
    return PersonalityFeedbackExport(
        feedback_id=record.feedback_id,
        feedback_digest=record.feedback_digest,
        kind=record.kind,
        response_trace_id=record.response_trace_id,
        payload=dict(record.payload),
        applied_task_id=record.applied_task_id,
        applied_revision=record.applied_revision,
        created_at=record.created_at,
    )


def revision_export(record: PersonalityProfileRevisionRecord) -> PersonalityRevisionExport:
    validate_sha256_digest(
        record.feedback_digest,
        field="exported personality revision feedback_digest",
    )
    validate_sha256_digest(
        record.proposal_digest,
        field="exported personality revision proposal_digest",
    )
    if not isinstance(record.preferences, list):
        raise PersonalityProfileLifecycleDataError("exported revision preferences are invalid")
    try:
        snapshot = PersonalitySnapshot(
            revision=record.revision,
            source=cast(PersonalitySource, record.source),
            preferences=tuple(preference_from_payload(item) for item in record.preferences),
            profile_digest=record.profile_digest,
        )
    except (TypeError, ValueError) as exc:
        raise PersonalityProfileLifecycleDataError(
            "exported personality revision is invalid"
        ) from exc
    if record.changed_dimension not in PERSONALITY_DIMENSIONS:
        raise PersonalityProfileLifecycleDataError(
            "exported personality changed dimension is invalid"
        )
    if not isinstance(record.conflict, bool):
        raise PersonalityProfileLifecycleDataError(
            "exported personality conflict flag is invalid"
        )
    return PersonalityRevisionExport(
        snapshot=snapshot,
        feedback_id=record.feedback_id,
        feedback_digest=record.feedback_digest,
        proposal_digest=record.proposal_digest,
        task_id=record.task_id,
        changed_dimension=cast(PersonalityDimension, record.changed_dimension),
        conflict=record.conflict,
        created_at=record.created_at,
    )


def preference_from_payload(value: object) -> PersonalityPreference:
    if not isinstance(value, dict):
        raise ValueError("personality preference is not an object")
    if set(value) != {"confidence", "dimension", "evidence_count", "value"}:
        raise ValueError("personality preference fields are invalid")
    return PersonalityPreference(
        dimension=cast(PersonalityDimension, value["dimension"]),
        value=cast(float, value["value"]),
        confidence=cast(float, value["confidence"]),
        evidence_count=cast(int, value["evidence_count"]),
    )


def lifecycle_event(record: PersonalityProfileLifecycleRecord) -> PersonalityLifecycleEvent:
    if record.operation not in {"reset", "delete"}:
        raise PersonalityProfileLifecycleDataError("persisted lifecycle operation is invalid")
    expected_digest = validate_sha256_digest(
        record.expected_profile_digest,
        field="personality lifecycle expected_profile_digest",
    )
    target_digest = validate_sha256_digest(
        record.target_profile_digest,
        field="personality lifecycle target_profile_digest",
    )
    revisions = (record.expected_revision, record.target_revision)
    if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in revisions):
        raise PersonalityProfileLifecycleDataError("persisted lifecycle revisions are invalid")
    data_digest = (
        None
        if record.data_state_digest is None
        else validate_sha256_digest(
            record.data_state_digest,
            field="personality lifecycle data_state_digest",
        )
    )
    if record.operation == "reset":
        if record.target_revision != record.expected_revision + 1 or data_digest is not None:
            raise PersonalityProfileLifecycleDataError("persisted reset transition is invalid")
    elif record.target_revision != 0 or data_digest is None:
        raise PersonalityProfileLifecycleDataError("persisted delete transition is invalid")
    validate_task_id(record.task_id)
    return PersonalityLifecycleEvent(
        operation=cast(PersonalityLifecycleOperation, record.operation),
        expected_revision=record.expected_revision,
        expected_profile_digest=expected_digest,
        target_revision=record.target_revision,
        target_profile_digest=target_digest,
        data_state_digest=data_digest,
        task_id=record.task_id,
        created_at=record.created_at,
    )
