"""Immutable contracts exchanged between Cortex and Bouche."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from mongars.autobiography.contracts import EvidenceKind, EvidenceSnapshot, GroundingStatus
from mongars.inference.base import ChatMessage, JsonValue

ResponseMode = Literal["answer", "clarify", "abstain"]


@dataclass(frozen=True, slots=True)
class DialoguePlan:
    trace_id: str
    session_id: UUID
    messages: tuple[ChatMessage, ...]
    model_alias: str
    model_digest: str | None
    options: dict[str, JsonValue]
    evidence: tuple[EvidenceSnapshot, ...]
    estimated_prompt_tokens: int
    context_budget: int
    response_mode: ResponseMode = "answer"
    require_web_citation: bool = False
    prompt_recipe_version: str = "bouche-v1"
    policy_version: str = "cortex-v1"


@dataclass(frozen=True, slots=True)
class CitationBinding:
    key: str
    kind: EvidenceKind
    source_id: str | None
    title: str | None
    source_uri: str | None
    locator: dict[str, JsonValue] | None


@dataclass(frozen=True, slots=True)
class ComposedResponse:
    answer: str
    model_alias: str
    model_digest: str | None
    finish_reason: str | None
    citations: tuple[CitationBinding, ...]
    grounding_status: GroundingStatus
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: float


__all__ = [
    "CitationBinding",
    "ComposedResponse",
    "DialoguePlan",
    "ResponseMode",
]
