from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySearchPayload(StrictPayload):
    query: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=8, ge=1, le=50)


class MemoryNoteCreatePayload(StrictPayload):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=500)
    sensitivity: str = Field(default="private", pattern="^(private|shared|restricted)$")
    retention_class: str = Field(default="keep", pattern="^(keep|ttl_30d|ttl_90d|legal_hold)$")


_PAYLOAD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "memory.search": TypeAdapter(MemorySearchPayload),
    "memory.note.create": TypeAdapter(MemoryNoteCreatePayload),
}

TASK_POLICY_KEYS: dict[str, tuple[str, str]] = {
    "memory.search": ("memory", "search"),
    "memory.note.create": ("memory", "note.create"),
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
    "MemoryNoteCreatePayload",
    "MemorySearchPayload",
    "UnsupportedTaskKind",
    "ValidationError",
    "normalize_task_payload",
]
