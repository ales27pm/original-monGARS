"""Bounded, deterministic explicit-feedback values for Mimétisme."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from mongars.orchestrator._cognitive_validation import validate_unit_interval
from mongars.orchestrator.personality import (
    PERSONALITY_DIMENSIONS,
    PersonalityDimension,
)

type FeedbackKind = Literal["correction", "helpfulness", "preference"]

_MAX_CORRECTION_CHARACTERS = 2_000
_MAX_CORRECTION_BYTES = 6_000
_TRACE_ID = re.compile(r"^trc_[0-9a-f]{32}$")
_FORBIDDEN_CONTROLS = frozenset(
    chr(code) for code in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)
)


@dataclass(frozen=True, slots=True)
class HelpfulnessFeedback:
    """Explicit binary feedback about one completed Cortex response."""

    feedback_id: UUID
    response_trace_id: str
    helpful: bool

    def __post_init__(self) -> None:
        _validate_feedback_id(self.feedback_id)
        object.__setattr__(
            self,
            "response_trace_id",
            _validate_trace_id(self.response_trace_id, required=True),
        )
        if not isinstance(self.helpful, bool):
            raise TypeError("helpfulness feedback must contain a boolean helpful value")

    def as_dict(self) -> dict[str, object]:
        return {
            "feedback_id": str(self.feedback_id),
            "helpful": self.helpful,
            "kind": "helpfulness",
            "response_trace_id": self.response_trace_id,
        }

    @property
    def feedback_digest(self) -> str:
        return _feedback_digest(self.as_dict())


@dataclass(frozen=True, slots=True)
class PreferenceFeedback:
    """A direct owner statement about one response-style dimension."""

    feedback_id: UUID
    dimension: PersonalityDimension
    desired_value: float
    response_trace_id: str | None = None

    def __post_init__(self) -> None:
        _validate_feedback_id(self.feedback_id)
        if self.dimension not in PERSONALITY_DIMENSIONS:
            raise ValueError("unsupported personality preference dimension")
        object.__setattr__(
            self,
            "desired_value",
            validate_unit_interval(
                self.desired_value,
                field="explicit preference desired_value",
            ),
        )
        object.__setattr__(
            self,
            "response_trace_id",
            _validate_trace_id(self.response_trace_id, required=False),
        )

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "desired_value": self.desired_value,
            "dimension": self.dimension,
            "feedback_id": str(self.feedback_id),
            "kind": "preference",
        }
        if self.response_trace_id is not None:
            payload["response_trace_id"] = self.response_trace_id
        return payload

    @property
    def feedback_digest(self) -> str:
        return _feedback_digest(self.as_dict())


@dataclass(frozen=True, slots=True)
class CorrectionFeedback:
    """An explicit corrected answer for later owner-reviewed learning workflows."""

    feedback_id: UUID
    response_trace_id: str
    correction_text: str

    def __post_init__(self) -> None:
        _validate_feedback_id(self.feedback_id)
        object.__setattr__(
            self,
            "response_trace_id",
            _validate_trace_id(self.response_trace_id, required=True),
        )
        object.__setattr__(
            self,
            "correction_text",
            _normalize_correction_text(self.correction_text),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "correction_text": self.correction_text,
            "feedback_id": str(self.feedback_id),
            "kind": "correction",
            "response_trace_id": self.response_trace_id,
        }

    @property
    def feedback_digest(self) -> str:
        return _feedback_digest(self.as_dict())


type ExplicitFeedback = CorrectionFeedback | HelpfulnessFeedback | PreferenceFeedback


def _validate_feedback_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError("feedback_id must be a UUID")
    return value


def _validate_trace_id(value: object, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("response_trace_id is required for this feedback kind")
        return None
    if not isinstance(value, str) or _TRACE_ID.fullmatch(value) is None:
        raise ValueError("response_trace_id must be a canonical Cortex trace identifier")
    return value


def _normalize_correction_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("correction_text must be a string")
    normalized = unicodedata.normalize("NFC", value)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(character in _FORBIDDEN_CONTROLS for character in normalized):
        raise ValueError("correction_text contains binary control characters")
    if not normalized:
        raise ValueError("correction_text must contain non-whitespace text")
    if len(normalized) > _MAX_CORRECTION_CHARACTERS:
        raise ValueError("correction_text exceeds the configured character limit")
    if len(normalized.encode("utf-8")) > _MAX_CORRECTION_BYTES:
        raise ValueError("correction_text exceeds the configured UTF-8 byte limit")
    return normalized


def _feedback_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


__all__ = [
    "CorrectionFeedback",
    "ExplicitFeedback",
    "FeedbackKind",
    "HelpfulnessFeedback",
    "PreferenceFeedback",
]
