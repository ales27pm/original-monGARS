from __future__ import annotations

import copy
from collections.abc import Callable
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mongars.adaptation.feedback import PreferenceFeedback
from mongars.adaptation.mimicry import (
    profile_delta_proposal_from_payload,
    propose_profile_delta,
)
from mongars.api.schemas import HelpfulnessFeedbackRequest, PreferenceFeedbackRequest
from mongars.rm.contracts import normalize_task_payload


def _proposal_payload() -> dict[str, object]:
    proposal = propose_profile_delta(
        None,
        PreferenceFeedback(
            feedback_id=uuid4(),
            dimension="technical_depth",
            desired_value=0.8,
        ),
    )
    assert proposal is not None
    return proposal.as_task_payload()


def test_profile_apply_task_round_trips_through_schema_and_domain_contract() -> None:
    payload = _proposal_payload()

    normalized = normalize_task_payload("personality.profile.apply", payload)
    proposal = profile_delta_proposal_from_payload(normalized)

    assert normalized == payload
    assert proposal.as_task_payload() == payload
    assert proposal.target_snapshot.revision == 1
    assert proposal.changed_dimension == "technical_depth"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update({"unexpected": True}),
        lambda payload: payload.__setitem__("conflict", True),
        lambda payload: payload.__setitem__("target_revision", 2),
        lambda payload: payload.__setitem__("target_profile_digest", "0" * 64),
    ],
)
def test_profile_apply_task_rejects_noncanonical_or_tampered_payloads(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    payload = _proposal_payload()
    mutated = copy.deepcopy(payload)
    mutation(mutated)

    with pytest.raises(ValidationError):
        normalize_task_payload("personality.profile.apply", mutated)


def test_profile_apply_task_rejects_duplicate_target_dimensions() -> None:
    payload = _proposal_payload()
    target_preferences = copy.deepcopy(payload["target_preferences"])
    assert isinstance(target_preferences, list)
    target_preferences.append(copy.deepcopy(target_preferences[0]))
    payload["target_preferences"] = target_preferences

    with pytest.raises(ValidationError):
        normalize_task_payload("personality.profile.apply", payload)


def test_profile_apply_task_rejects_boolean_numeric_substitutes() -> None:
    payload = _proposal_payload()
    proposed = copy.deepcopy(payload["proposed"])
    assert isinstance(proposed, dict)
    proposed["confidence"] = True
    payload["proposed"] = proposed

    with pytest.raises(ValidationError):
        normalize_task_payload("personality.profile.apply", payload)


def test_profile_apply_task_rejects_nonboolean_conflict() -> None:
    payload = _proposal_payload()
    payload["conflict"] = 0

    with pytest.raises(ValidationError):
        normalize_task_payload("personality.profile.apply", payload)


def test_preference_feedback_request_rejects_boolean_desired_value() -> None:
    with pytest.raises(ValidationError):
        PreferenceFeedbackRequest(
            kind="preference",
            feedback_id=uuid4(),
            dimension="brevity",
            desired_value=True,
        )


def test_helpfulness_feedback_request_rejects_integer_boolean_substitutes() -> None:
    with pytest.raises(ValidationError):
        HelpfulnessFeedbackRequest(
            kind="helpfulness",
            feedback_id=uuid4(),
            response_trace_id="trc_" + ("a" * 32),
            helpful=1,
        )
