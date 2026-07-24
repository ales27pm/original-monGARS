"""Typed contracts for auditable autobiographical events and generation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mongars.inference.base import JsonValue

TurnRole = Literal["user", "assistant", "policy"]
TurnState = Literal["accepted", "generating", "final", "failed", "cancelled", "redacted"]
Sensitivity = Literal["private", "shared", "restricted"]
RetentionClass = Literal["keep", "ttl_30d", "ttl_90d", "legal_hold"]
GenerationStatus = Literal["started", "completed", "failed", "cancelled"]
GroundingStatus = Literal["not_required", "grounded", "partially_grounded", "abstained"]
EvidenceKind = Literal["memory", "web", "conversation", "policy"]


@dataclass(frozen=True, slots=True)
class StoredTurn:
    id: UUID
    owner_id: str
    session_id: UUID
    ordinal: int
    trace_id: str
    role: TurnRole
    content: str
    state: TurnState
    sensitivity: Sensitivity
    retention_class: RetentionClass
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    key: str
    kind: EvidenceKind
    text: str
    source_id: str | None = None
    title: str | None = None
    source_uri: str | None = None
    locator: dict[str, JsonValue] | None = None
    score: float | None = None
    rank: int = 0
    retrieved_at: datetime | None = None
    included: bool = True


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionStartedPayload(StrictPayload):
    session_id: UUID


class TurnAcceptedPayload(StrictPayload):
    turn_id: UUID
    role: TurnRole
    ordinal: int = Field(ge=1)
    character_count: int = Field(ge=0)


class RetrievalCompletedPayload(StrictPayload):
    candidate_count: int = Field(ge=0)
    included_count: int = Field(ge=0)
    evidence_keys: list[str] = Field(default_factory=list, max_length=256)


class WebSearchCompletedPayload(StrictPayload):
    status: str = Field(min_length=1, max_length=64)
    result_count: int = Field(ge=0)
    evidence_keys: list[str] = Field(default_factory=list, max_length=64)


class GenerationStartedPayload(StrictPayload):
    generation_run_id: UUID
    user_turn_id: UUID
    model_alias: str = Field(min_length=1, max_length=255)
    model_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_recipe_version: str = Field(min_length=1, max_length=64)
    policy_version: str = Field(min_length=1, max_length=64)
    evidence_count: int = Field(ge=0)


class GenerationCompletedPayload(StrictPayload):
    generation_run_id: UUID
    assistant_turn_id: UUID
    grounding_status: GroundingStatus
    citation_keys: list[str] = Field(default_factory=list, max_length=256)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)


class GenerationFailedPayload(StrictPayload):
    generation_run_id: UUID
    error_code: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")
    retryable: bool


class GenerationCancelledPayload(StrictPayload):
    generation_run_id: UUID
    reason: str = Field(min_length=1, max_length=100)


class AssistantTurnCommittedPayload(StrictPayload):
    turn_id: UUID
    generation_run_id: UUID
    ordinal: int = Field(ge=1)


class FeedbackReceivedPayload(StrictPayload):
    target_turn_id: UUID
    rating: Literal["up", "down", "neutral"]
    tags: list[str] = Field(default_factory=list, max_length=32)


class CorrectionReceivedPayload(StrictPayload):
    target_turn_id: UUID
    correction_id: UUID
    character_count: int = Field(ge=1)


_EVENT_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "session_started": TypeAdapter(SessionStartedPayload),
    "user_turn_accepted": TypeAdapter(TurnAcceptedPayload),
    "retrieval_completed": TypeAdapter(RetrievalCompletedPayload),
    "web_search_completed": TypeAdapter(WebSearchCompletedPayload),
    "generation_started": TypeAdapter(GenerationStartedPayload),
    "generation_completed": TypeAdapter(GenerationCompletedPayload),
    "generation_failed": TypeAdapter(GenerationFailedPayload),
    "generation_cancelled": TypeAdapter(GenerationCancelledPayload),
    "assistant_turn_committed": TypeAdapter(AssistantTurnCommittedPayload),
    "feedback_received": TypeAdapter(FeedbackReceivedPayload),
    "correction_received": TypeAdapter(CorrectionReceivedPayload),
}


def normalize_event_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate one registered event payload and return canonical JSON data."""

    adapter = _EVENT_ADAPTERS.get(event_type)
    if adapter is None:
        raise ValueError(f"unsupported autobiographical event type: {event_type}")
    validated = adapter.validate_python(payload)
    if not isinstance(validated, BaseModel):
        raise TypeError("autobiographical event adapter returned an invalid value")
    return validated.model_dump(mode="json")


__all__ = [
    "EvidenceKind",
    "EvidenceSnapshot",
    "GenerationStatus",
    "GroundingStatus",
    "RetentionClass",
    "Sensitivity",
    "StoredTurn",
    "TurnRole",
    "TurnState",
    "normalize_event_payload",
]
