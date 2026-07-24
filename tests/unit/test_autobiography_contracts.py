from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from mongars.autobiography.contracts import normalize_event_payload


def test_normalizes_registered_event_payload() -> None:
    run_id = uuid4()
    turn_id = uuid4()
    payload = normalize_event_payload(
        "generation_started",
        {
            "generation_run_id": run_id,
            "user_turn_id": turn_id,
            "model_alias": "qwen3:4b-instruct",
            "model_digest": "a" * 64,
            "prompt_recipe_version": "bouche-v1",
            "policy_version": "cortex-v1",
            "evidence_count": 3,
        },
    )

    assert payload["generation_run_id"] == str(run_id)
    assert payload["user_turn_id"] == str(turn_id)
    assert payload["model_digest"] == "a" * 64


def test_rejects_unknown_event_type() -> None:
    with pytest.raises(ValueError, match="unsupported autobiographical event type"):
        normalize_event_payload("made_up_event", {})


def test_rejects_extra_or_malformed_event_fields() -> None:
    with pytest.raises(ValidationError):
        normalize_event_payload(
            "generation_failed",
            {
                "generation_run_id": uuid4(),
                "error_code": "Bad Code",
                "retryable": False,
                "secret": "must not persist",
            },
        )


def test_evidence_key_must_match_kind_and_locator_is_defensively_copied() -> None:
    from mongars.autobiography.contracts import EvidenceSnapshot

    locator = {"page_number": 7}
    snapshot = EvidenceSnapshot(
        key="M1",
        kind="memory",
        text="  grounded evidence  ",
        locator=locator,
    )
    locator["page_number"] = 99

    assert snapshot.text == "grounded evidence"
    assert snapshot.locator is not None
    assert snapshot.locator["page_number"] == 7
    with pytest.raises(TypeError):
        snapshot.locator["page_number"] = 8  # type: ignore[index]

    with pytest.raises(ValueError, match="prefix does not match"):
        EvidenceSnapshot(key="W1", kind="memory", text="bad prefix")
