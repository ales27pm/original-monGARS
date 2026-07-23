"""API response contracts for personality export and lifecycle receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast
from uuid import UUID

from mongars.adaptation.lifecycle import (
    PersonalityExportBundle,
    PersonalityFeedbackExport,
    PersonalityLifecycleEvent,
    PersonalityRevisionExport,
)
from mongars.api.schemas import ApiModel, PersonalityProfileResponse
from mongars.orchestrator.personality import PersonalityDimension


class PersonalityFeedbackExportResponse(ApiModel):
    feedback_id: UUID
    feedback_digest: str
    kind: Literal["correction", "helpfulness", "preference"]
    response_trace_id: str | None
    payload: dict[str, Any]
    applied_task_id: UUID | None
    applied_revision: int | None
    created_at: datetime

    @classmethod
    def from_export(cls, item: PersonalityFeedbackExport) -> PersonalityFeedbackExportResponse:
        return cls(
            feedback_id=item.feedback_id,
            feedback_digest=item.feedback_digest,
            kind=cast(Literal["correction", "helpfulness", "preference"], item.kind),
            response_trace_id=item.response_trace_id,
            payload=item.payload,
            applied_task_id=item.applied_task_id,
            applied_revision=item.applied_revision,
            created_at=item.created_at,
        )


class PersonalityRevisionExportResponse(ApiModel):
    profile: PersonalityProfileResponse
    feedback_id: UUID
    feedback_digest: str
    proposal_digest: str
    task_id: UUID
    changed_dimension: PersonalityDimension
    conflict: bool
    created_at: datetime

    @classmethod
    def from_export(cls, item: PersonalityRevisionExport) -> PersonalityRevisionExportResponse:
        return cls(
            profile=PersonalityProfileResponse.from_snapshot(item.snapshot),
            feedback_id=item.feedback_id,
            feedback_digest=item.feedback_digest,
            proposal_digest=item.proposal_digest,
            task_id=item.task_id,
            changed_dimension=item.changed_dimension,
            conflict=item.conflict,
            created_at=item.created_at,
        )


class PersonalityLifecycleEventResponse(ApiModel):
    operation: Literal["reset", "delete"]
    expected_revision: int
    expected_profile_digest: str
    target_revision: int
    target_profile_digest: str
    data_state_digest: str | None
    task_id: UUID
    created_at: datetime

    @classmethod
    def from_event(cls, item: PersonalityLifecycleEvent) -> PersonalityLifecycleEventResponse:
        return cls(
            operation=item.operation,
            expected_revision=item.expected_revision,
            expected_profile_digest=item.expected_profile_digest,
            target_revision=item.target_revision,
            target_profile_digest=item.target_profile_digest,
            data_state_digest=item.data_state_digest,
            task_id=item.task_id,
            created_at=item.created_at,
        )


class PersonalityProfileExportResponse(ApiModel):
    schema_version: Literal["mongars-personality-export-v1"] = "mongars-personality-export-v1"
    exported_at: datetime
    profile: PersonalityProfileResponse
    revisions: list[PersonalityRevisionExportResponse]
    lifecycle_events: list[PersonalityLifecycleEventResponse]
    feedback: list[PersonalityFeedbackExportResponse]

    @classmethod
    def from_bundle(cls, bundle: PersonalityExportBundle) -> PersonalityProfileExportResponse:
        return cls(
            exported_at=bundle.exported_at,
            profile=PersonalityProfileResponse.from_snapshot(bundle.profile),
            revisions=[
                PersonalityRevisionExportResponse.from_export(item)
                for item in bundle.revisions
            ],
            lifecycle_events=[
                PersonalityLifecycleEventResponse.from_event(item)
                for item in bundle.lifecycle_events
            ],
            feedback=[
                PersonalityFeedbackExportResponse.from_export(item)
                for item in bundle.feedback
            ],
        )


__all__ = [
    "PersonalityFeedbackExportResponse",
    "PersonalityLifecycleEventResponse",
    "PersonalityProfileExportResponse",
    "PersonalityRevisionExportResponse",
]
