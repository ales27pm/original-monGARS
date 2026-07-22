from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mongars.api.schemas import TaskDetailResponse, TaskPayloadPageResponse, TaskResponse
from mongars.db.models import TaskQueue
from mongars.rm.payload_view import task_payload_page


def _task() -> TaskQueue:
    now = datetime.now(UTC)
    task = TaskQueue(
        owner_id="owner",
        kind="memory.ingest_text",
        risk_level="local_mutation",
        status="waiting_approval",
        priority=100,
        attempt_count=0,
        max_attempts=3,
        run_after=now,
        trace_id="trace-1",
        payload={"text": "Exact protected note", "sensitivity": "private"},
        action_digest="a" * 64,
        approval_expires_at=now + timedelta(minutes=10),
    )
    task.id = uuid4()
    task.created_at = now
    task.updated_at = now
    return task


def test_task_summary_omits_protected_payload() -> None:
    response = TaskResponse.from_model(_task()).model_dump(mode="json")

    assert "payload" not in response
    assert "action_digest" not in response


def test_task_detail_exposes_only_bounded_payload_summary_and_integrity_digest() -> None:
    task = _task()

    response = TaskDetailResponse.from_model(task)
    dumped = response.model_dump(mode="json")

    assert "payload" not in dumped
    assert response.payload_summary.top_level_field_count == 2
    assert response.payload_summary.preview_omitted_characters == 0
    assert "Exact protected note" in response.payload_summary.preview_head
    assert response.action_digest == task.action_digest


def test_task_payload_page_keeps_task_identity_and_integrity_digest() -> None:
    task = _task()

    response = TaskPayloadPageResponse.from_rendered(
        task=task,
        page=task_payload_page(task.payload, page_index=0),
    )

    assert response.task_id == task.id
    assert response.action_digest == task.action_digest
    assert response.page_index == 0
    assert response.page_count == 1
    assert response.character_end == len(response.content)
