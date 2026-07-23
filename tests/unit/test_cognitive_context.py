from __future__ import annotations

import json
import math

import pytest

from mongars.orchestrator.cognitive_context import serialize_cognitive_context
from mongars.orchestrator.emotion import AffectSignal
from mongars.orchestrator.personality import (
    PersonalityPreference,
    PersonalitySnapshot,
)

_DIGEST = "a" * 64


def test_unavailable_affect_is_explicit_and_content_free() -> None:
    signal = AffectSignal.unavailable()

    assert signal.as_dict() == {
        "advisory_only": True,
        "confidence": 0.0,
        "evidence_count": 0,
        "kind": "affect_signal",
        "label": "unknown",
        "source": "unknown",
    }


@pytest.mark.parametrize("confidence", [-0.1, 1.1, math.nan, math.inf, True])
def test_affect_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence"):
        AffectSignal(
            label="joy",
            confidence=confidence,  # type: ignore[arg-type]
            source="explicit_feedback",
            evidence_count=1,
        )


def test_reviewed_model_affect_requires_pinned_identity() -> None:
    signal = AffectSignal(
        label="neutral",
        confidence=0.75,
        source="reviewed_model",
        evidence_count=3,
        model_alias="emotion-model:v1",
        model_digest=_DIGEST,
    )

    assert signal.as_dict()["model_digest"] == _DIGEST
    with pytest.raises(ValueError, match="model_digest"):
        AffectSignal(
            label="neutral",
            confidence=0.75,
            source="reviewed_model",
            evidence_count=3,
            model_alias="emotion-model:v1",
            model_digest="latest",
        )


def test_non_model_affect_rejects_model_identity() -> None:
    with pytest.raises(ValueError, match="only valid"):
        AffectSignal(
            label="joy",
            confidence=1.0,
            source="explicit_feedback",
            evidence_count=1,
            model_alias="unexpected",
        )


def test_default_personality_is_empty_revision_zero() -> None:
    assert PersonalitySnapshot.default().as_dict() == {
        "advisory_only": True,
        "kind": "personality_snapshot",
        "preferences": [],
        "revision": 0,
        "scale": "0=low,1=high",
        "schema_version": "personality-v1",
        "source": "default",
    }


def test_personality_orders_dimensions_and_serializes_stably() -> None:
    snapshot = PersonalitySnapshot(
        revision=2,
        source="approved_profile",
        profile_digest=_DIGEST,
        preferences=(
            PersonalityPreference(
                dimension="technical_depth",
                value=0.9,
                confidence=0.8,
                evidence_count=4,
            ),
            PersonalityPreference(
                dimension="brevity",
                value=0.7,
                confidence=1.0,
                evidence_count=5,
            ),
        ),
    )

    assert [item.dimension for item in snapshot.preferences] == ["brevity", "technical_depth"]
    first = serialize_cognitive_context(personality=snapshot)
    second = serialize_cognitive_context(personality=snapshot)
    assert first == second
    assert first is not None
    payload = json.loads(first)
    assert payload["trust"] == "untrusted_owner_reviewed_context"
    assert payload["personality"]["profile_digest"] == _DIGEST


def test_personality_rejects_duplicate_dimensions() -> None:
    preference = PersonalityPreference(
        dimension="directness",
        value=0.5,
        confidence=0.8,
        evidence_count=2,
    )
    with pytest.raises(ValueError, match="unique"):
        PersonalitySnapshot(
            revision=1,
            source="explicit_feedback",
            preferences=(preference, preference),
            profile_digest=_DIGEST,
        )


@pytest.mark.parametrize("value", [-1, 2, math.nan, False])
def test_personality_preference_rejects_invalid_scores(value: object) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        PersonalityPreference(
            dimension="humor",
            value=value,  # type: ignore[arg-type]
            confidence=0.5,
            evidence_count=1,
        )


def test_serializer_returns_none_without_context() -> None:
    assert serialize_cognitive_context() is None


def test_serializer_enforces_utf8_byte_limit() -> None:
    snapshot = PersonalitySnapshot(
        revision=1,
        source="approved_profile",
        profile_digest=_DIGEST,
        preferences=(
            PersonalityPreference(
                dimension="initiative",
                value=0.5,
                confidence=0.5,
                evidence_count=1,
            ),
        ),
    )

    with pytest.raises(ValueError, match="byte limit"):
        serialize_cognitive_context(personality=snapshot, maximum_bytes=32)
