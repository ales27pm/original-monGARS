from __future__ import annotations

import pytest
from pydantic import ValidationError

from mongars.adaptation.mimicry import EMPTY_PROFILE_DIGEST
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.rm.contracts import normalize_task_payload


def test_approved_profile_can_represent_a_versioned_empty_reset() -> None:
    snapshot = PersonalitySnapshot(
        revision=7,
        source="approved_profile",
        preferences=(),
        profile_digest=EMPTY_PROFILE_DIGEST,
    )

    assert snapshot.revision == 7
    assert snapshot.preferences == ()
    assert snapshot.profile_digest == EMPTY_PROFILE_DIGEST


def test_explicit_feedback_profile_still_requires_a_preference() -> None:
    with pytest.raises(ValueError, match="at least one preference"):
        PersonalitySnapshot(
            revision=1,
            source="explicit_feedback",
            preferences=(),
            profile_digest=EMPTY_PROFILE_DIGEST,
        )


def test_reset_payload_is_closed_and_canonical() -> None:
    payload = {
        "expected_profile_digest": "a" * 64,
        "expected_revision": 4,
        "target_profile_digest": EMPTY_PROFILE_DIGEST,
        "target_revision": 5,
    }

    assert normalize_task_payload("personality.profile.reset", payload) == payload

    with pytest.raises(ValidationError):
        normalize_task_payload(
            "personality.profile.reset",
            {**payload, "target_revision": 6},
        )
    with pytest.raises(ValidationError):
        normalize_task_payload(
            "personality.profile.reset",
            {**payload, "expected_revision": True},
        )
    with pytest.raises(ValidationError):
        normalize_task_payload(
            "personality.profile.reset",
            {**payload, "extra": "forbidden"},
        )


def test_delete_payload_requires_complete_privacy_deletion() -> None:
    payload = {
        "data_state_digest": "b" * 64,
        "delete_feedback": True,
        "delete_history": True,
        "delete_tasks": True,
        "expected_profile_digest": "c" * 64,
        "expected_revision": 8,
    }

    assert normalize_task_payload("personality.profile.delete", payload) == payload

    for field in ("delete_feedback", "delete_history", "delete_tasks"):
        with pytest.raises(ValidationError):
            normalize_task_payload(
                "personality.profile.delete",
                {**payload, field: False},
            )
    with pytest.raises(ValidationError):
        normalize_task_payload(
            "personality.profile.delete",
            {**payload, "data_state_digest": "not-a-digest"},
        )
