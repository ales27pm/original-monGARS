from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from mongars.adaptation.mimicry import profile_delta_proposal_from_payload


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySearchPayload(StrictPayload):
    query: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=8, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace text")
        return value


class MemoryNoteCreatePayload(StrictPayload):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=500)
    sensitivity: str = Field(default="private", pattern="^(private|shared|restricted)$")
    retention_class: str = Field(default="keep", pattern="^(keep|ttl_30d|ttl_90d|legal_hold)$")


class MemoryReindexPayload(StrictPayload):
    document_id: UUID | None = None
    batch_size: int = Field(default=32, ge=1, le=128)


class DocumentIngestPayload(StrictPayload):
    staging_id: UUID
    original_filename: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detected_mime_type: Literal[
        "text/plain",
        "text/markdown",
        "text/html",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    byte_size: int = Field(ge=1, le=20_000_000)
    source_timestamp: datetime
    received_at: datetime
    source_time_basis: Literal["user_supplied"]
    title: str | None = Field(default=None, max_length=500)
    sensitivity: str = Field(default="private", pattern="^(private|shared|restricted)$")
    retention_class: str = Field(default="keep", pattern="^(keep|ttl_30d|ttl_90d|legal_hold)$")

    @field_validator("source_timestamp")
    @classmethod
    def normalize_source_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(UTC)


type PersonalityDimensionValue = Literal[
    "brevity",
    "directness",
    "formality",
    "humor",
    "initiative",
    "technical_depth",
]


class PersonalityPreferencePayload(StrictPayload):
    dimension: PersonalityDimensionValue
    value: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=1, le=10_000)

    @field_validator("value", "confidence", mode="before")
    @classmethod
    def reject_boolean_scores(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("personality scores must not be booleans")
        return value

    @field_validator("evidence_count", mode="before")
    @classmethod
    def reject_boolean_evidence(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("personality evidence_count must not be a boolean")
        return value


class PersonalityProfileApplyPayload(StrictPayload):
    changed_dimension: PersonalityDimensionValue
    conflict: bool
    expected_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0, le=2_147_483_646)
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    feedback_id: UUID
    previous: PersonalityPreferencePayload | None
    proposed: PersonalityPreferencePayload
    target_preferences: list[PersonalityPreferencePayload] = Field(min_length=1, max_length=6)
    target_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_revision: int = Field(ge=1, le=2_147_483_647)

    @field_validator("expected_revision", "target_revision", mode="before")
    @classmethod
    def reject_boolean_revisions(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("personality revisions must not be booleans")
        return value

    @model_validator(mode="after")
    def validate_profile_delta(self) -> PersonalityProfileApplyPayload:
        profile_delta_proposal_from_payload(self.model_dump(mode="json"))
        return self


_PAYLOAD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "memory.search": TypeAdapter(MemorySearchPayload),
    "memory.note.create": TypeAdapter(MemoryNoteCreatePayload),
    "memory.reindex": TypeAdapter(MemoryReindexPayload),
    "document.ingest": TypeAdapter(DocumentIngestPayload),
    "personality.profile.apply": TypeAdapter(PersonalityProfileApplyPayload),
}

TASK_POLICY_KEYS: dict[str, tuple[str, str]] = {
    "memory.search": ("memory", "search"),
    "memory.note.create": ("memory", "note.create"),
    "memory.reindex": ("memory", "reindex"),
    "document.ingest": ("document", "ingest"),
    "personality.profile.apply": ("personality", "profile.apply"),
}


class UnsupportedTaskKind(ValueError):
    pass


def normalize_task_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    adapter = _PAYLOAD_ADAPTERS.get(kind)
    if adapter is None:
        raise UnsupportedTaskKind(f"unsupported task kind: {kind}")
    validated = adapter.validate_python(payload)
    if not isinstance(validated, BaseModel):
        raise TypeError("task payload adapter returned an invalid value")
    return validated.model_dump(mode="json")


__all__ = [
    "TASK_POLICY_KEYS",
    "DocumentIngestPayload",
    "MemoryNoteCreatePayload",
    "MemoryReindexPayload",
    "MemorySearchPayload",
    "PersonalityPreferencePayload",
    "PersonalityProfileApplyPayload",
    "UnsupportedTaskKind",
    "ValidationError",
    "normalize_task_payload",
]
