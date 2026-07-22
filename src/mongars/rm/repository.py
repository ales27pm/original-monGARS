from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, case, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import TaskQueue
from mongars.ids import uuid7


@dataclass(frozen=True, slots=True)
class TaskTransition:
    task_id: UUID
    owner_id: str
    trace_id: str
    kind: str
    status: str
    error_text: str | None


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_id: str,
        kind: str,
        risk_level: str,
        status: str,
        trace_id: str,
        payload: dict[str, Any],
        action_digest: str | None,
        approval_expires_at: datetime | None,
        priority: int = 100,
        max_attempts: int = 3,
        dedupe_key: str | None = None,
    ) -> TaskQueue:
        task = TaskQueue(
            owner_id=owner_id,
            kind=kind,
            risk_level=risk_level,
            status=status,
            trace_id=trace_id,
            payload=payload,
            action_digest=action_digest,
            approval_expires_at=approval_expires_at,
            priority=priority,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def get_for_owner(
        self,
        *,
        task_id: UUID,
        owner_id: str,
        for_update: bool = False,
    ) -> TaskQueue | None:
        statement = select(TaskQueue).where(
            TaskQueue.id == task_id,
            TaskQueue.owner_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(TaskQueue | None, await self._session.scalar(statement))

    async def get_for_worker(self, *, task_id: UUID, for_update: bool = False) -> TaskQueue | None:
        statement = select(TaskQueue).where(TaskQueue.id == task_id)
        if for_update:
            statement = statement.with_for_update()
        return cast(TaskQueue | None, await self._session.scalar(statement))

    async def recover_expired_leases(self) -> list[TaskTransition]:
        now = datetime.now(UTC)
        statement = (
            select(TaskQueue)
            .where(
                TaskQueue.status == "running",
                TaskQueue.lease_expires_at.is_not(None),
                TaskQueue.lease_expires_at < now,
            )
            .order_by(TaskQueue.lease_expires_at, TaskQueue.id)
            .with_for_update(skip_locked=True)
        )
        tasks = list((await self._session.scalars(statement)).all())
        transitions: list[TaskTransition] = []
        for task in tasks:
            exhausted = task.attempt_count >= task.max_attempts
            task.status = "failed" if exhausted else "queued"
            task.error_text = (
                "worker lease expired after final attempt; task failed"
                if exhausted
                else "worker lease expired; task requeued"
            )
            task.lease_expires_at = None
            task.execution_token = None
            task.updated_at = now
            transitions.append(
                TaskTransition(
                    task_id=task.id,
                    owner_id=task.owner_id,
                    trace_id=task.trace_id,
                    kind=task.kind,
                    status=task.status,
                    error_text=task.error_text,
                )
            )
        await self._session.flush()
        return transitions

    async def claim_next(self, *, lease_seconds: int) -> TaskQueue | None:
        now = datetime.now(UTC)
        statement = (
            select(TaskQueue)
            .where(
                TaskQueue.status == "queued",
                TaskQueue.run_after <= now,
                TaskQueue.attempt_count < TaskQueue.max_attempts,
            )
            .order_by(TaskQueue.priority.desc(), TaskQueue.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        task = await self._session.scalar(statement)
        if task is None:
            return None
        task.status = "running"
        task.attempt_count += 1
        task.lease_expires_at = now + timedelta(seconds=lease_seconds)
        task.execution_token = uuid7()
        task.error_text = None
        await self._session.flush()
        return task

    async def heartbeat(
        self,
        *,
        task_id: UUID,
        execution_token: UUID,
        lease_seconds: int,
    ) -> bool:
        now = datetime.now(UTC)
        statement = (
            update(TaskQueue)
            .where(
                TaskQueue.id == task_id,
                TaskQueue.status == "running",
                TaskQueue.execution_token == execution_token,
                TaskQueue.lease_expires_at > now,
            )
            .values(
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return bool(result.rowcount)

    async def lock_owned_execution(
        self,
        *,
        task_id: UUID,
        execution_token: UUID,
    ) -> bool:
        """Lock a live attempt for a short, idempotent local-effect transaction."""

        now = datetime.now(UTC)
        statement = (
            select(TaskQueue.id)
            .where(
                TaskQueue.id == task_id,
                TaskQueue.status == "running",
                TaskQueue.execution_token == execution_token,
                TaskQueue.lease_expires_at > now,
            )
            .with_for_update()
        )
        return await self._session.scalar(statement) is not None

    async def mark_done_if_owned(
        self,
        *,
        task_id: UUID,
        execution_token: UUID,
        result: dict[str, Any],
    ) -> TaskTransition | None:
        now = datetime.now(UTC)
        statement = (
            update(TaskQueue)
            .where(
                TaskQueue.id == task_id,
                TaskQueue.status == "running",
                TaskQueue.execution_token == execution_token,
                TaskQueue.lease_expires_at > now,
            )
            .values(
                status="done",
                result=result,
                error_text=None,
                lease_expires_at=None,
                execution_token=None,
                updated_at=now,
            )
            .returning(
                TaskQueue.id,
                TaskQueue.owner_id,
                TaskQueue.trace_id,
                TaskQueue.kind,
                TaskQueue.status,
                TaskQueue.error_text,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        return _transition_from_row(row)

    async def mark_failed_if_owned(
        self,
        *,
        task_id: UUID,
        execution_token: UUID,
        error: str,
        terminal: bool,
    ) -> TaskTransition | None:
        now = datetime.now(UTC)
        safe_error = error[:2_000]
        status_expression = (
            "failed"
            if terminal
            else case(
                (TaskQueue.attempt_count < TaskQueue.max_attempts, "queued"),
                else_="failed",
            )
        )
        run_after_expression = (
            TaskQueue.run_after
            if terminal
            else case(
                (TaskQueue.attempt_count < TaskQueue.max_attempts, now + timedelta(seconds=2)),
                else_=TaskQueue.run_after,
            )
        )
        statement = (
            update(TaskQueue)
            .where(
                TaskQueue.id == task_id,
                TaskQueue.status == "running",
                TaskQueue.execution_token == execution_token,
                TaskQueue.lease_expires_at > now,
            )
            .values(
                status=status_expression,
                run_after=run_after_expression,
                error_text=safe_error,
                lease_expires_at=None,
                execution_token=None,
                updated_at=now,
            )
            .returning(
                TaskQueue.id,
                TaskQueue.owner_id,
                TaskQueue.trace_id,
                TaskQueue.kind,
                TaskQueue.status,
                TaskQueue.error_text,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        return _transition_from_row(row)

    async def mark_done(self, task: TaskQueue, *, result: dict[str, Any]) -> None:
        task.status = "done"
        task.result = result
        task.error_text = None
        task.lease_expires_at = None
        task.execution_token = None
        await self._session.flush()

    async def mark_failed(self, task: TaskQueue, *, error: str) -> None:
        now = datetime.now(UTC)
        safe_error = error[:2_000]
        if task.attempt_count < task.max_attempts:
            task.status = "queued"
            task.run_after = now + timedelta(seconds=min(2**task.attempt_count, 60))
        else:
            task.status = "failed"
        task.error_text = safe_error
        task.lease_expires_at = None
        task.execution_token = None
        await self._session.flush()

    async def mark_terminal_failed(self, task: TaskQueue, *, error: str) -> None:
        task.status = "failed"
        task.error_text = error[:2_000]
        task.lease_expires_at = None
        task.execution_token = None
        await self._session.flush()

    async def list_for_owner(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        statuses: set[str] | None = None,
    ) -> list[TaskQueue]:
        conditions = [TaskQueue.owner_id == owner_id]
        if statuses:
            conditions.append(TaskQueue.status.in_(statuses))
        statement = (
            select(TaskQueue)
            .where(and_(*conditions))
            .order_by(TaskQueue.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all())

    async def cancel(self, *, task_id: UUID, owner_id: str) -> TaskQueue | None:
        task = await self.get_for_owner(task_id=task_id, owner_id=owner_id, for_update=True)
        if task is None:
            return None
        if task.status not in {"queued", "waiting_approval"}:
            raise ValueError(f"task in state {task.status!r} cannot be cancelled")
        task.status = "cancelled"
        task.lease_expires_at = None
        task.execution_token = None
        await self._session.flush()
        return task

    async def count_active_for_owner(self, *, owner_id: str) -> int:
        statement = select(TaskQueue.id).where(
            TaskQueue.owner_id == owner_id,
            or_(
                TaskQueue.status == "queued",
                TaskQueue.status == "running",
                TaskQueue.status == "waiting_approval",
            ),
        )
        return len((await self._session.scalars(statement)).all())


def _transition_from_row(row: Any | None) -> TaskTransition | None:
    if row is None:
        return None
    return TaskTransition(
        task_id=cast(UUID, row.id),
        owner_id=cast(str, row.owner_id),
        trace_id=cast(str, row.trace_id),
        kind=cast(str, row.kind),
        status=cast(str, row.status),
        error_text=cast(str | None, row.error_text),
    )
