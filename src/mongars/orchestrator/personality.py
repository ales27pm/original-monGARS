"""Immutable, owner-reviewed response-style preferences for Cortex prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from mongars.orchestrator._cognitive_validation import (
    validate_sha256_digest,
    validate_unit_interval,
)

type PersonalityDimension = Literal[
    "brevity",
    "directness",
    "formality",
    "humor",
    "initiative",
    "technical_depth",
]
type PersonalitySource = Literal["approved_profile", "default", "explicit_feedback"]

PERSONALITY_DIMENSIONS = frozenset(
    {"brevity", "directness", "formality", "humor", "initiative", "technical_depth"}
)
_PERSONALITY_SOURCES = frozenset({"approved_profile", "default", "explicit_feedback"})
_PROFILE_SCHEMA = "personality-v1"


@dataclass(frozen=True, slots=True)
class PersonalityPreference:
    """One reviewed preference on a normalized zero-to-one response-style scale."""

    dimension: PersonalityDimension
    value: float
    confidence: float
    evidence_count: int

    def __post_init__(self) -> None:
        if self.dimension not in PERSONALITY_DIMENSIONS:
            raise ValueError("unsupported personality dimension")
        object.__setattr__(
            self,
            "value",
            validate_unit_interval(self.value, field="personality value"),
        )
        object.__setattr__(
            self,
            "confidence",
            validate_unit_interval(self.confidence, field="personality confidence"),
        )
        if (
            isinstance(self.evidence_count, bool)
            or not isinstance(self.evidence_count, int)
            or not 1 <= self.evidence_count <= 10_000
        ):
            raise ValueError("personality evidence_count must be between 1 and 10000")

    def as_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "dimension": self.dimension,
            "evidence_count": self.evidence_count,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class PersonalitySnapshot:
    """One immutable, versioned snapshot of explicitly reviewed preferences."""

    revision: int
    source: PersonalitySource
    preferences: tuple[PersonalityPreference, ...] = ()
    profile_digest: str | None = None
    schema_version: str = _PROFILE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _PROFILE_SCHEMA:
            raise ValueError("unsupported personality schema version")
        if self.source not in _PERSONALITY_SOURCES:
            raise ValueError("unsupported personality source")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or not 0 <= self.revision <= 2_147_483_647
        ):
            raise ValueError("personality revision must be a nonnegative integer")
        if not isinstance(self.preferences, tuple):
            raise ValueError("personality preferences must be an immutable tuple")
        if len(self.preferences) > len(PERSONALITY_DIMENSIONS):
            raise ValueError("personality snapshot has too many preferences")
        if any(not isinstance(item, PersonalityPreference) for item in self.preferences):
            raise ValueError("personality preferences contain an invalid value")

        ordered = tuple(sorted(self.preferences, key=lambda preference: preference.dimension))
        dimensions = tuple(preference.dimension for preference in ordered)
        if len(set(dimensions)) != len(dimensions):
            raise ValueError("personality dimensions must be unique")
        object.__setattr__(self, "preferences", ordered)

        if self.source == "default":
            if self.revision != 0 or ordered or self.profile_digest is not None:
                raise ValueError("default personality must be empty revision zero")
            return

        if self.revision < 1:
            raise ValueError("reviewed personality requires a positive revision")
        if self.source == "explicit_feedback" and not ordered:
            raise ValueError("explicit-feedback personality requires at least one preference")
        digest = validate_sha256_digest(
            self.profile_digest,
            field="reviewed personality profile_digest",
        )
        object.__setattr__(self, "profile_digest", digest)

    @classmethod
    def default(cls) -> PersonalitySnapshot:
        return cls(revision=0, source="default")

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "advisory_only": True,
            "kind": "personality_snapshot",
            "preferences": [preference.as_dict() for preference in self.preferences],
            "revision": self.revision,
            "scale": "0=low,1=high",
            "schema_version": self.schema_version,
            "source": self.source,
        }
        if self.profile_digest is not None:
            payload["profile_digest"] = self.profile_digest
        return payload


__all__ = [
    "PERSONALITY_DIMENSIONS",
    "PersonalityDimension",
    "PersonalityPreference",
    "PersonalitySnapshot",
    "PersonalitySource",
]
