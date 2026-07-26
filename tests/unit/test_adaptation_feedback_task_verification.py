from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from mongars.adaptation.feedback import PreferenceFeedback
from mongars.api.routes.adaptation import _verify_feedback_task
from mongars.db.models import TaskQueue


def _task_with_feedback_id(feedback_id: str, *, risk_level: str = "local_mutation") -> TaskQueue:
    return TaskQueue(
        owner_id="owner-1",
        kind="personality.profile.apply",
        risk_level=risk_level,
        trace_id="trace-verify-feedback-task",
        payload={"feedback_id": feedback_id},
    )


def test_verify_feedback_task_accepts_feedback_uuid_formats() -> None:
    feedback_id = uuid4()
    for payload_feedback_id in (str(feedback_id), feedback_id.hex):
        task = _task_with_feedback_id(payload_feedback_id)
        feedback = PreferenceFeedback(
            feedback_id=feedback_id,
            dimension="technical_depth",
            desired_value=0.85,
        )

        _verify_feedback_task(task, feedback)


def test_verify_feedback_task_rejects_invalid_feedback_uuid_payload() -> None:
    task = _task_with_feedback_id("not-a-uuid")
    feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="technical_depth",
        desired_value=0.85,
    )

    with pytest.raises(HTTPException) as caught:
        _verify_feedback_task(task, feedback)

    assert caught.value.status_code == status.HTTP_409_CONFLICT
    assert caught.value.detail == "existing feedback task feedback_id is invalid"


def test_verify_feedback_task_rejects_mismatched_feedback_uuid() -> None:
    feedback_id = uuid4()
    task = _task_with_feedback_id(str(uuid4()))
    feedback = PreferenceFeedback(
        feedback_id=feedback_id,
        dimension="technical_depth",
        desired_value=0.85,
    )

    with pytest.raises(HTTPException) as caught:
        _verify_feedback_task(task, feedback)

    assert caught.value.status_code == status.HTTP_409_CONFLICT
    assert caught.value.detail == "existing feedback task does not match this feedback"
