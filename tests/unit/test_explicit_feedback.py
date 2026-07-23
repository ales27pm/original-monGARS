from __future__ import annotations

import json
import math
from dataclasses import replace
from uuid import uuid4

import pytest

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.adaptation.mimicry import (
    EMPTY_PROFILE_DIGEST,
    MAX_PROFILE_DELTA_BYTES,
    personality_profile_digest,
    propose_profile_delta,
)
from mongars.orchestrator.personality import (
    PersonalityPreference,
    PersonalitySnapshot,
)

_TRACE_ID = "trc_" + ("a" * 32)


def _reviewed_snapshot(*preferences: PersonalityPreference) -> PersonalitySnapshot:
    ordered = tuple(sorted(preferences, key=lambda preference: preference.dimension))
    return PersonalitySnapshot(
        revision=3,
        source="approved_profile",
        preferences=ordered,
        profile_digest=personality_profile_digest(ordered),
    )


def test_helpfulness_feedback_requires_a_canonical_response_trace() -> None:
    feedback = HelpfulnessFeedback(
        feedback_id=uuid4(),
        response_trace_id=_TRACE_ID,
        helpful=True,
    )

    assert feedback.as_dict()["kind"] == "helpfulness"
    assert len(feedback.feedback_digest) == 64
    with pytest.raises(ValueError, match="canonical Cortex trace"):
        HelpfulnessFeedback(
            feedback_id=uuid4(),
            response_trace_id="trace-from-client",
            helpful=True,
        )


def test_helpfulness_feedback_rejects_integer_boolean_substitutes() -> None:
    with pytest.raises(TypeError, match="boolean"):
        HelpfulnessFeedback(
            feedback_id=uuid4(),
            response_trace_id=_TRACE_ID,
            helpful=1,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("value", [-0.1, 1.1, math.nan, math.inf, True])
def test_preference_feedback_rejects_invalid_desired_values(value: object) -> None:
    with pytest.raises(ValueError, match="between 0 and 1"):
        PreferenceFeedback(
            feedback_id=uuid4(),
            dimension="brevity",
            desired_value=value,  # type: ignore[arg-type]
        )


def test_preference_feedback_digest_is_stable_and_content_sensitive() -> None:
    feedback_id = uuid4()
    first = PreferenceFeedback(
        feedback_id=feedback_id,
        dimension="technical_depth",
        desired_value=0.8,
        response_trace_id=_TRACE_ID,
    )
    duplicate = PreferenceFeedback(
        feedback_id=feedback_id,
        dimension="technical_depth",
        desired_value=0.8,
        response_trace_id=_TRACE_ID,
    )
    changed = PreferenceFeedback(
        feedback_id=feedback_id,
        dimension="technical_depth",
        desired_value=0.2,
        response_trace_id=_TRACE_ID,
    )

    assert first.feedback_digest == duplicate.feedback_digest
    assert first.feedback_digest != changed.feedback_digest


def test_correction_feedback_normalizes_unicode_and_line_endings() -> None:
    feedback = CorrectionFeedback(
        feedback_id=uuid4(),
        response_trace_id=_TRACE_ID,
        correction_text="  cafe\u0301\r\nsecond line  ",
    )

    assert feedback.correction_text == "café\nsecond line"
    assert feedback.as_dict()["correction_text"] == "café\nsecond line"


def test_correction_feedback_enforces_character_and_utf8_byte_limits() -> None:
    with pytest.raises(ValueError, match="character limit"):
        CorrectionFeedback(
            feedback_id=uuid4(),
            response_trace_id=_TRACE_ID,
            correction_text="x" * 2_001,
        )
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        CorrectionFeedback(
            feedback_id=uuid4(),
            response_trace_id=_TRACE_ID,
            correction_text="😀" * 2_000,
        )


def test_ambiguous_feedback_never_proposes_a_profile_mutation() -> None:
    current = _reviewed_snapshot(
        PersonalityPreference(
            dimension="brevity",
            value=0.5,
            confidence=1.0,
            evidence_count=2,
        )
    )

    assert (
        propose_profile_delta(
            current,
            HelpfulnessFeedback(
                feedback_id=uuid4(),
                response_trace_id=_TRACE_ID,
                helpful=False,
            ),
        )
        is None
    )
    assert (
        propose_profile_delta(
            current,
            CorrectionFeedback(
                feedback_id=uuid4(),
                response_trace_id=_TRACE_ID,
                correction_text="Use the revised calculation.",
            ),
        )
        is None
    )


def test_first_direct_preference_creates_a_reviewable_revision_one_delta() -> None:
    feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="brevity",
        desired_value=0.75,
    )

    proposal = propose_profile_delta(None, feedback)

    assert proposal is not None
    assert proposal.expected_revision == 0
    assert proposal.expected_profile_digest == EMPTY_PROFILE_DIGEST
    assert proposal.target_snapshot.revision == 1
    assert proposal.target_snapshot.source == "explicit_feedback"
    assert proposal.previous is None
    assert proposal.proposed == PersonalityPreference(
        dimension="brevity",
        value=0.75,
        confidence=1.0,
        evidence_count=1,
    )
    assert proposal.conflict is False
    assert proposal.target_snapshot.profile_digest == personality_profile_digest(
        proposal.target_snapshot.preferences
    )


def test_repeated_same_preference_strengthens_evidence_without_conflict() -> None:
    current = _reviewed_snapshot(
        PersonalityPreference(
            dimension="directness",
            value=0.8,
            confidence=0.7,
            evidence_count=4,
        )
    )
    feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="directness",
        desired_value=0.8,
    )

    proposal = propose_profile_delta(current, feedback)

    assert proposal is not None
    assert proposal.conflict is False
    assert proposal.previous is current.preferences[0]
    assert proposal.proposed.evidence_count == 5
    assert proposal.proposed.confidence == 1.0


def test_contradictory_preference_is_flagged_for_exact_review() -> None:
    current = _reviewed_snapshot(
        PersonalityPreference(
            dimension="humor",
            value=0.9,
            confidence=1.0,
            evidence_count=8,
        )
    )
    feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="humor",
        desired_value=0.1,
    )

    proposal = propose_profile_delta(current, feedback)

    assert proposal is not None
    assert proposal.conflict is True
    assert proposal.previous is current.preferences[0]
    assert proposal.proposed.value == 0.1
    assert proposal.proposed.evidence_count == 1
    payload = proposal.as_task_payload()
    assert payload["conflict"] is True
    assert payload["previous"] == current.preferences[0].as_dict()
    assert payload["proposed"] == proposal.proposed.as_dict()


def test_proposal_preserves_unrelated_dimensions_and_does_not_mutate_input() -> None:
    current = _reviewed_snapshot(
        PersonalityPreference(
            dimension="brevity",
            value=0.6,
            confidence=0.8,
            evidence_count=3,
        ),
        PersonalityPreference(
            dimension="technical_depth",
            value=0.7,
            confidence=0.9,
            evidence_count=5,
        ),
    )
    original = current.preferences

    proposal = propose_profile_delta(
        current,
        PreferenceFeedback(
            feedback_id=uuid4(),
            dimension="brevity",
            desired_value=0.9,
        ),
    )

    assert proposal is not None
    assert current.preferences == original
    preserved = next(
        preference
        for preference in proposal.target_snapshot.preferences
        if preference.dimension == "technical_depth"
    )
    assert preserved is original[1]


def test_current_profile_digest_mismatch_fails_closed() -> None:
    current = _reviewed_snapshot(
        PersonalityPreference(
            dimension="initiative",
            value=0.5,
            confidence=0.8,
            evidence_count=2,
        )
    )
    tampered = replace(current, profile_digest="f" * 64)

    with pytest.raises(ValueError, match="digest does not match"):
        propose_profile_delta(
            tampered,
            PreferenceFeedback(
                feedback_id=uuid4(),
                dimension="initiative",
                desired_value=0.6,
            ),
        )


def test_profile_delta_payload_is_deterministic_bounded_and_content_addressed() -> None:
    proposal = propose_profile_delta(
        None,
        PreferenceFeedback(
            feedback_id=uuid4(),
            dimension="formality",
            desired_value=0.4,
        ),
    )
    assert proposal is not None

    first = proposal.as_task_payload()
    second = proposal.as_task_payload()
    canonical = json.dumps(
        first,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert first == second
    assert len(canonical) <= MAX_PROFILE_DELTA_BYTES
    assert len(proposal.proposal_digest) == 64
    assert first["feedback_digest"] == proposal.feedback_digest
    assert first["target_profile_digest"] == proposal.target_snapshot.profile_digest
