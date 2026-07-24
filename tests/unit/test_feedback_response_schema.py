from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from mongars.api.schemas import (
    ExplicitFeedbackCreateResponse,
    PersonalitySnapshotResponse,
    TaskResponse,
)


def test_explicit_feedback_response_includes_profile_and_proposal_task() -> None:
    now = datetime.now(UTC)
    task = TaskResponse(
        id=uuid4(),
        kind="personality.profile.apply",
        risk_level="local_mutation",
        status="waiting_approval",
        trace_id="fb_" + ("a" * 32),
        priority=250,
        attempt_count=0,
        max_attempts=3,
        result=None,
        error_text=None,
        approval_expires_at=now,
        approved_at=None,
        created_at=now,
        updated_at=now,
    )
    response = ExplicitFeedbackCreateResponse(
        feedback_id=uuid4(),
        kind="preference",
        feedback_digest="b" * 64,
        created=True,
        applied_task_id=None,
        applied_revision=None,
        proposal={"changed_dimension": "technical_depth"},
        profile=PersonalitySnapshotResponse(
            revision=0,
            source="default",
            profile_digest=None,
            preferences=(),
        ),
        proposal_task=task,
    )

    payload = response.model_dump(mode="json")
    assert payload["profile"]["revision"] == 0
    assert payload["proposal_task"]["id"] == str(task.id)
    assert payload["proposal_task"]["kind"] == "personality.profile.apply"
