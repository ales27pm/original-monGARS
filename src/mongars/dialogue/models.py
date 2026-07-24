"""Immutable contracts exchanged between Cortex and Bouche."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from mongars.autobiography.contracts import EvidenceKind, EvidenceSnapshot, GroundingStatus
from mongars.inference.base import ChatMessage, JsonValue

ResponseMode = Literal["answer", "clarify", "abstain"]
_MODEL_DIGEST = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DialoguePlan:
    trace_id: str
    session_id: UUID
    messages: tuple[ChatMessage, ...]
    model_alias: str
    model_digest: str | None
    options: Mapping[str, JsonValue]
    evidence: tuple[EvidenceSnapshot, ...]
    estimated_prompt_tokens: int
    context_budget: int
    response_mode: ResponseMode = "answer"
    require_web_citation: bool = False
    prompt_recipe_version: str = "bouche-v1"
    policy_version: str = "cortex-v1"

    def __post_init__(self) -> None:
        if not self.trace_id or len(self.trace_id) > 128:
            raise ValueError("dialogue trace_id must be non-empty and at most 128 characters")
        if not self.messages or self.messages[-1].role != "user":
            raise ValueError("dialogue messages must end with the current user message")
        if not self.model_alias.strip() or self.model_alias != self.model_alias.strip():
            raise ValueError("dialogue model_alias must be a trimmed non-empty string")
        if self.model_digest is not None and _MODEL_DIGEST.fullmatch(self.model_digest) is None:
            raise ValueError("dialogue model_digest must be lowercase SHA-256 hexadecimal")
        if self.context_budget <= 0 or self.estimated_prompt_tokens < 0:
            raise ValueError("dialogue prompt budget metadata is invalid")
        if self.estimated_prompt_tokens > self.context_budget:
            raise ValueError("dialogue prompt exceeds the approved context budget")
        if not self.prompt_recipe_version or len(self.prompt_recipe_version) > 64:
            raise ValueError("dialogue prompt recipe version is invalid")
        if not self.policy_version or len(self.policy_version) > 64:
            raise ValueError("dialogue policy version is invalid")
        if not isinstance(self.require_web_citation, bool):
            raise TypeError("dialogue web citation flag must be boolean")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))
        keys = [item.key for item in self.evidence]
        if len(keys) != len(set(keys)):
            raise ValueError("dialogue evidence keys must be unique")
        if self.require_web_citation and not any(
            item.kind == "web" and item.included for item in self.evidence
        ):
            raise ValueError("required web citation needs included web evidence")


@dataclass(frozen=True, slots=True)
class CitationBinding:
    key: str
    kind: EvidenceKind
    source_id: str | None
    title: str | None
    source_uri: str | None
    locator: Mapping[str, JsonValue] | None


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
