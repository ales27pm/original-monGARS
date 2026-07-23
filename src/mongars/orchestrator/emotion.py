"""Immutable, advisory affect context for Cortex prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from mongars.orchestrator._cognitive_validation import (
    validate_sha256_digest,
    validate_unit_interval,
)

type AffectLabel = Literal[
    "anger",
    "disgust",
    "fear",
    "joy",
    "mixed",
    "neutral",
    "sadness",
    "surprise",
    "unknown",
]
type AffectSource = Literal[
    "deterministic_rule",
    "explicit_feedback",
    "reviewed_model",
    "unknown",
]

_AFFECT_LABELS = frozenset(
    {
        "anger",
        "disgust",
        "fear",
        "joy",
        "mixed",
        "neutral",
        "sadness",
        "surprise",
        "unknown",
    }
)
_AFFECT_SOURCES = frozenset(
    {"deterministic_rule", "explicit_feedback", "reviewed_model", "unknown"}
)
_MODEL_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")


@dataclass(frozen=True, slots=True)
class AffectSignal:
    """One bounded affect observation that is never authoritative for policy.

    The signal may influence response tone only after Cortex explicitly serializes it
    as untrusted advisory prompt context. It must never affect authentication,
    authorization, approval, retention, safety, or backend selection.
    """

    label: AffectLabel
    confidence: float
    source: AffectSource
    evidence_count: int
    model_alias: str | None = None
    model_digest: str | None = None

    def __post_init__(self) -> None:
        if self.label not in _AFFECT_LABELS:
            raise ValueError("unsupported affect label")
        if self.source not in _AFFECT_SOURCES:
            raise ValueError("unsupported affect source")
        object.__setattr__(
            self,
            "confidence",
            validate_unit_interval(self.confidence, field="affect confidence"),
        )
        if (
            isinstance(self.evidence_count, bool)
            or not isinstance(self.evidence_count, int)
            or not 0 <= self.evidence_count <= 10_000
        ):
            raise ValueError("affect evidence_count must be between 0 and 10000")

        unavailable = self.label == "unknown" or self.source == "unknown"
        if unavailable:
            if (
                self.label != "unknown"
                or self.source != "unknown"
                or self.confidence != 0.0
                or self.evidence_count != 0
            ):
                raise ValueError(
                    "unknown affect provenance is reserved for the zero-evidence unavailable value"
                )
        elif self.evidence_count < 1:
            raise ValueError("observed affect requires at least one evidence item")

        if self.source == "reviewed_model":
            alias = _validate_model_alias(self.model_alias)
            digest = validate_sha256_digest(
                self.model_digest,
                field="reviewed affect model_digest",
            )
            object.__setattr__(self, "model_alias", alias)
            object.__setattr__(self, "model_digest", digest)
        elif self.model_alias is not None or self.model_digest is not None:
            raise ValueError("model identity is only valid for reviewed_model affect")

    @classmethod
    def unavailable(cls) -> AffectSignal:
        """Return an explicit no-observation value rather than guessing affect."""

        return cls(
            label="unknown",
            confidence=0.0,
            source="unknown",
            evidence_count=0,
        )

    def as_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-safe representation without source text."""

        payload: dict[str, object] = {
            "advisory_only": True,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "kind": "affect_signal",
            "label": self.label,
            "source": self.source,
        }
        if self.model_alias is not None:
            payload["model_alias"] = self.model_alias
        if self.model_digest is not None:
            payload["model_digest"] = self.model_digest
        return payload


def _validate_model_alias(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _MODEL_ALIAS.fullmatch(value):
        raise ValueError("reviewed affect model_alias is invalid")
    return value


__all__ = ["AffectLabel", "AffectSignal", "AffectSource"]
