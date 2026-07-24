"""Typed contracts for auditable autobiographical events and generation evidence."""

from __future__ import annotations

import copy
import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, NoReturn, cast
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

_EVIDENCE_KEY = re.compile(r"^[HMWP][1-9][0-9]{0,2}$")
_EVIDENCE_PREFIX: dict[EvidenceKind, str] = {
    "conversation": "H",
    "memory": "M",
    "web": "W",
    "policy": "P",
}
_MAX_EVIDENCE_TEXT_BYTES = 1_000_000


class _FrozenJsonDict(dict[str, JsonValue]):
    """JSON-serializable mapping that rejects every normal mutation path."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("frozen JSON mapping is immutable")

    def __setitem__(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def __delitem__(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def pop(self, *args: Any, **kwargs: Any) -> JsonValue:
        self._immutable()

    def popitem(self) -> tuple[str, JsonValue]:
        self._immutable()

    def setdefault(self, *args: Any, **kwargs: Any) -> JsonValue:
        self._immutable()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def __ior__(self, *args: Any, **kwargs: Any) -> _FrozenJsonDict:
        self._immutable()


class _FrozenJsonList(list[JsonValue]):
    """JSON-serializable sequence that rejects every normal mutation path."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("frozen JSON sequence is immutable")

    def __setitem__(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def __delitem__(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def append(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def extend(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def insert(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def pop(self, *args: Any, **kwargs: Any) -> JsonValue:
        self._immutable()

    def remove(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def reverse(self) -> None:
        self._immutable()

    def sort(self, *args: Any, **kwargs: Any) -> None:
        self._immutable()

    def __iadd__(self, *args: Any, **kwargs: Any) -> _FrozenJsonList:
        self._immutable()

    def __imul__(self, *args: Any, **kwargs: Any) -> _FrozenJsonList:
        self._immutable()


def deep_freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    """Return a recursive immutable deep copy that remains JSON serializable."""

    if not isinstance(value, Mapping):
        raise TypeError("JSON value must be a mapping")
    copied = _deep_copy_json(value)
    frozen = _deep_freeze_json(copied)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded by mapping input
        raise TypeError("JSON mapping freeze produced an invalid result")
    return cast(Mapping[str, JsonValue], frozen)


def _deep_copy_json(value: object) -> object:
    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON mapping keys must be strings")
            copied[key] = _deep_copy_json(item)
        return copied
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_deep_copy_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return copy.deepcopy(value)
    raise TypeError("JSON value contains an unsupported type")


def _deep_freeze_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return _FrozenJsonDict(
            {
                key: _deep_freeze_json(item)
                for key, item in value.items()
                if isinstance(key, str)
            }
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _FrozenJsonList(_deep_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError("JSON value contains an unsupported type")


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
    locator: Mapping[str, JsonValue] | None = None
    score: float | None = None
    rank: int = 0
    retrieved_at: datetime | None = None
    included: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or _EVIDENCE_KEY.fullmatch(self.key) is None:
            raise ValueError("evidence key must match H/M/W/P followed by a positive index")
        if self.key[0] != _EVIDENCE_PREFIX[self.kind]:
            raise ValueError("evidence key prefix does not match evidence kind")
        if not isinstance(self.text, str):
            raise TypeError("evidence text must be a string")
        normalized = unicodedata.normalize(
            "NFC",
            self.text.replace("\r\n", "\n").replace("\r", "\n"),
        ).strip()
        if not normalized:
            raise ValueError("evidence text must not be empty")
        if len(normalized.encode("utf-8")) > _MAX_EVIDENCE_TEXT_BYTES:
            raise ValueError("evidence text exceeds the hard byte ceiling")
        object.__setattr__(self, "text", normalized)

        if isinstance(self.rank, bool) or not isinstance(self.rank, int) or self.rank < 0:
            raise ValueError("evidence rank must be a non-negative integer")
        if self.score is not None and (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(float(self.score))
        ):
            raise ValueError("evidence score must be a finite number")
        if self.retrieved_at is not None and (
            self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None
        ):
            raise ValueError("evidence retrieval time must be timezone-aware")
        if not isinstance(self.included, bool):
            raise TypeError("evidence included flag must be boolean")
        if self.locator is not None:
            if not isinstance(self.locator, Mapping):
                raise TypeError("evidence locator must be a mapping")
            object.__setattr__(self, "locator", deep_freeze_json_mapping(self.locator))


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
    "deep_freeze_json_mapping",
    "normalize_event_payload",
]
