from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator


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


_PAYLOAD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "memory.search": TypeAdapter(MemorySearchPayload),
    "memory.note.create": TypeAdapter(MemoryNoteCreatePayload),
    "memory.reindex": TypeAdapter(MemoryReindexPayload),
    "document.ingest": TypeAdapter(DocumentIngestPayload),
}

TASK_POLICY_KEYS: dict[str, tuple[str, str]] = {
    "memory.search": ("memory", "search"),
    "memory.note.create": ("memory", "note.create"),
    "memory.reindex": ("memory", "reindex"),
    "document.ingest": ("document", "ingest"),
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
    "UnsupportedTaskKind",
    "ValidationError",
    "normalize_task_payload",
]
