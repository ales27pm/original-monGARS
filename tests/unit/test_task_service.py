from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from mongars.config import Environment, Settings
from mongars.db.models import TaskQueue
from mongars.events.repository import EventRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskIntegrityError, TaskService, TaskStateError
from mongars.security.policy import ActionClassification, ToolPolicy


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[UUID, TaskQueue] = {}
        self.owner_lock_requests: list[tuple[UUID, str, bool]] = []

    async def create(self, **values: Any) -> TaskQueue:
        task = TaskQueue(
            id=uuid4(),
            approved_at=None,
            consumed_at=None,
            error_text=None,
            **values,
        )
        self.tasks[task.id] = task
        return task

    async def get_for_owner(
        self,
        *,
        task_id: UUID,
        owner_id: str,
        for_update: bool = False,
    ) -> TaskQueue | None:
        self.owner_lock_requests.append((task_id, owner_id, for_update))
        task = self.tasks.get(task_id)
        if task is None or task.owner_id != owner_id:
            return None
        return task


class FakeEventRepository:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **values: Any) -> object:
        self.records.append(values)
        return object()


@dataclass(slots=True)
class ServiceHarness:
    service: TaskService
    repository: FakeTaskRepository
    events: FakeEventRepository


@pytest.fixture
def harness() -> ServiceHarness:
    settings = Settings(
        environment=Environment.TEST,
        approval_hmac_key=SecretStr(secrets.token_hex(32)),
        approval_ttl_seconds=300,
    )
    repository = FakeTaskRepository()
    events = FakeEventRepository()
    policy = ToolPolicy(
        {
            ("memory", "search"): ActionClassification.READ_ONLY,
            ("memory", "note.create"): ActionClassification.LOCAL_MUTATION,
        }
    )
    service = TaskService(
        settings=settings,
        repository=cast(TaskRepository, repository),
        events=cast(EventRepository, events),
        policy=policy,
    )
    return ServiceHarness(service=service, repository=repository, events=events)


@pytest.mark.asyncio
async def test_read_only_task_is_queued_without_approval(harness: ServiceHarness) -> None:
    task = await harness.service.create(
        owner_id="owner-1",
        kind="memory.search",
        payload={"query": "project notes"},
    )

    assert task.status == "queued"
    assert task.risk_level == ActionClassification.READ_ONLY.value
    assert task.payload == {"query": "project notes", "top_k": 8}
    assert task.action_digest is None
    assert task.approval_expires_at is None

    harness.service.verify_for_execution(task)
    assert task.consumed_at is None
    assert harness.events.records[-1]["event_type"] == "task_created"


@pytest.mark.asyncio
async def test_local_mutation_waits_with_digest_bound_to_normalized_payload(
    harness: ServiceHarness,
) -> None:
    before = datetime.now(UTC)

    task = await harness.service.create(
        owner_id="owner-1",
        kind="memory.note.create",
        payload={"text": "remember this"},
    )

    assert task.status == "waiting_approval"
    assert task.risk_level == ActionClassification.LOCAL_MUTATION.value
    assert task.payload == {
        "text": "remember this",
        "title": None,
        "sensitivity": "private",
        "retention_class": "keep",
    }
    assert task.action_digest is not None and len(task.action_digest) == 64
    assert task.approval_expires_at is not None
    assert task.approval_expires_at >= before + timedelta(seconds=299)


@pytest.mark.asyncio
async def test_expired_approval_is_rejected_and_task_is_cancelled(
    harness: ServiceHarness,
) -> None:
    task = await _create_mutation(harness)
    task.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(TaskStateError, match="approval has expired"):
        await harness.service.approve(owner_id=task.owner_id, task_id=task.id)

    assert task.status == "cancelled"
    assert task.error_text == "approval expired"
    assert harness.repository.owner_lock_requests[-1] == (task.id, task.owner_id, True)


@pytest.mark.asyncio
async def test_payload_tampering_before_approval_invalidates_digest(
    harness: ServiceHarness,
) -> None:
    task = await _create_mutation(harness)
    task.payload = {**task.payload, "text": "attacker changed this"}

    with pytest.raises(TaskIntegrityError, match="approval is no longer valid"):
        await harness.service.approve(owner_id=task.owner_id, task_id=task.id)

    assert task.status == "failed"
    assert task.error_text == "approval action digest mismatch"


@pytest.mark.asyncio
async def test_payload_tampering_after_approval_fails_execution(
    harness: ServiceHarness,
) -> None:
    task = await _create_mutation(harness)
    approved = await harness.service.approve(owner_id=task.owner_id, task_id=task.id)
    assert approved is task
    task.payload = {**task.payload, "title": "changed after approval"}

    with pytest.raises(TaskIntegrityError, match="payload changed after approval"):
        harness.service.verify_for_execution(task)

    assert task.consumed_at is None


@pytest.mark.asyncio
async def test_consumed_approval_cannot_be_replayed(harness: ServiceHarness) -> None:
    task = await _create_mutation(harness)
    await harness.service.approve(owner_id=task.owner_id, task_id=task.id)

    harness.service.verify_for_execution(task)
    consumed_at = task.consumed_at
    assert consumed_at is not None

    with pytest.raises(TaskIntegrityError, match="already been consumed"):
        harness.service.verify_for_execution(task)

    assert task.consumed_at == consumed_at


@pytest.mark.asyncio
async def test_approval_request_cannot_be_replayed(harness: ServiceHarness) -> None:
    task = await _create_mutation(harness)
    await harness.service.approve(owner_id=task.owner_id, task_id=task.id)

    with pytest.raises(TaskStateError, match="task is queued, not waiting_approval"):
        await harness.service.approve(owner_id=task.owner_id, task_id=task.id)

    approval_events = [
        record for record in harness.events.records if record["event_type"] == "task_approved"
    ]
    assert len(approval_events) == 1


@pytest.mark.asyncio
async def test_approval_expiring_before_execution_is_rejected(
    harness: ServiceHarness,
) -> None:
    task = await _create_mutation(harness)
    await harness.service.approve(owner_id=task.owner_id, task_id=task.id)
    task.approval_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(TaskIntegrityError, match="expired before execution"):
        harness.service.verify_for_execution(task)

    assert task.consumed_at is None


@pytest.mark.asyncio
async def test_changing_privileged_task_risk_to_read_only_does_not_bypass_approval(
    harness: ServiceHarness,
) -> None:
    task = await _create_mutation(harness)
    task.risk_level = ActionClassification.READ_ONLY.value

    with pytest.raises(TaskIntegrityError, match="risk classification changed"):
        harness.service.verify_for_execution(task)

    assert task.consumed_at is None


@pytest.mark.asyncio
async def test_changing_read_only_kind_to_mutation_does_not_bypass_approval(
    harness: ServiceHarness,
) -> None:
    task = await harness.service.create(
        owner_id="owner-1",
        kind="memory.search",
        payload={"query": "project notes"},
    )
    task.kind = "memory.note.create"
    task.payload = {
        "text": "silently write this",
        "title": None,
        "sensitivity": "private",
        "retention_class": "keep",
    }

    with pytest.raises(TaskIntegrityError, match="risk classification changed"):
        harness.service.verify_for_execution(task)

    assert task.consumed_at is None


@pytest.mark.asyncio
async def test_approval_is_owner_scoped(harness: ServiceHarness) -> None:
    task = await _create_mutation(harness)

    result = await harness.service.approve(owner_id="different-owner", task_id=task.id)

    assert result is None
    assert task.status == "waiting_approval"
    assert task.approved_at is None


async def _create_mutation(harness: ServiceHarness) -> TaskQueue:
    return await harness.service.create(
        owner_id="owner-1",
        kind="memory.note.create",
        payload={"text": "original text"},
    )
