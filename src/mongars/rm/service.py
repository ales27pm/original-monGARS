from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mongars.config import Settings
from mongars.db.models import TaskQueue
from mongars.events.repository import EventRepository
from mongars.rm.contracts import TASK_POLICY_KEYS, normalize_task_payload
from mongars.rm.repository import TaskRepository
from mongars.security.policy import PolicyDecision, ToolPolicy


class TaskStateError(ValueError):
    pass


class TaskIntegrityError(RuntimeError):
    pass


class TaskReviewMismatchError(TaskIntegrityError):
    pass


class TaskService:
    POLICY_VERSION = "2026-07-22.1"

    def __init__(
        self,
        *,
        settings: Settings,
        repository: TaskRepository,
        events: EventRepository,
        policy: ToolPolicy,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._events = events
        self._policy = policy
        self._hmac_key = settings.approval_hmac_key.get_secret_value().encode()

    def _digest(
        self,
        *,
        owner_id: str,
        kind: str,
        payload: dict[str, Any],
        expires_at: datetime,
    ) -> str:
        canonical = json.dumps(
            {
                "owner_id": owner_id,
                "kind": kind,
                "payload": payload,
                "policy_version": self.POLICY_VERSION,
                "expires_at": expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        return hmac.new(self._hmac_key, canonical, hashlib.sha256).hexdigest()

    async def create(
        self,
        *,
        owner_id: str,
        kind: str,
        payload: dict[str, Any],
        priority: int = 100,
        max_attempts: int = 3,
        dedupe_key: str | None = None,
    ) -> TaskQueue:
        normalized_payload = normalize_task_payload(kind, payload)
        tool, action = TASK_POLICY_KEYS[kind]
        policy_result = self._policy.evaluate(tool, action)
        if policy_result.decision is PolicyDecision.DENY or policy_result.classification is None:
            raise PermissionError(policy_result.reason)

        trace_id = f"trc_{secrets.token_hex(16)}"
        requires_approval = policy_result.requires_approval
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=self._settings.approval_ttl_seconds)
            if requires_approval
            else None
        )
        digest = (
            self._digest(
                owner_id=owner_id,
                kind=kind,
                payload=normalized_payload,
                expires_at=expires_at,
            )
            if expires_at is not None
            else None
        )
        task = await self._repository.create(
            owner_id=owner_id,
            kind=kind,
            risk_level=policy_result.classification.value,
            status="waiting_approval" if requires_approval else "queued",
            trace_id=trace_id,
            payload=normalized_payload,
            action_digest=digest,
            approval_expires_at=expires_at,
            priority=priority,
            max_attempts=max_attempts,
            dedupe_key=dedupe_key,
        )
        await self._events.record(
            owner_id=owner_id,
            trace_id=trace_id,
            actor="cortex",
            event_type="task_created",
            summary=f"Created {kind} task in {task.status} state",
            payload={"task_id": str(task.id), "risk_level": task.risk_level},
        )
        return task

    async def approve(
        self,
        *,
        owner_id: str,
        task_id: UUID,
        reviewed_action_digest: str,
    ) -> TaskQueue | None:
        task = await self._repository.get_for_owner(
            task_id=task_id, owner_id=owner_id, for_update=True
        )
        if task is None:
            return None
        if task.status != "waiting_approval":
            raise TaskStateError(f"task is {task.status}, not waiting_approval")
        now = datetime.now(UTC)
        expires_at = task.approval_expires_at
        if expires_at is None or expires_at <= now:
            task.status = "cancelled"
            task.error_text = "approval expired"
            raise TaskStateError("approval has expired")
        expected = self._digest(
            owner_id=owner_id,
            kind=task.kind,
            payload=task.payload,
            expires_at=expires_at,
        )
        if task.action_digest is None or not hmac.compare_digest(expected, task.action_digest):
            task.status = "failed"
            task.error_text = "approval action digest mismatch"
            raise TaskIntegrityError("task approval is no longer valid")
        if not hmac.compare_digest(reviewed_action_digest, expected):
            raise TaskReviewMismatchError("reviewed action digest does not match this task")
        task.status = "queued"
        task.approved_at = now
        await self._events.record(
            owner_id=owner_id,
            trace_id=task.trace_id,
            actor="user",
            event_type="task_approved",
            summary=f"Approved {task.kind} task",
            payload={"task_id": str(task.id)},
        )
        return task

    def verify_for_execution(
        self,
        task: TaskQueue,
        *,
        allow_consumed_approval: bool = False,
    ) -> None:
        try:
            normalized_payload = normalize_task_payload(task.kind, task.payload)
            tool, action = TASK_POLICY_KEYS[task.kind]
        except (KeyError, ValueError) as exc:
            raise TaskIntegrityError("task kind or payload is no longer valid") from exc
        policy_result = self._policy.evaluate(tool, action)
        if policy_result.decision is PolicyDecision.DENY or policy_result.classification is None:
            raise TaskIntegrityError("task action is no longer permitted by policy")
        if policy_result.classification.value != task.risk_level:
            raise TaskIntegrityError("task risk classification changed after creation")
        if normalized_payload != task.payload:
            raise TaskIntegrityError("task payload is not in canonical validated form")

        if policy_result.decision is PolicyDecision.ALLOW:
            return
        if not policy_result.requires_approval:
            raise TaskIntegrityError("task policy decision is not executable")
        now = datetime.now(UTC)
        if task.approved_at is None or task.approval_expires_at is None:
            raise TaskIntegrityError("privileged task has no approval")
        if task.consumed_at is None and task.approval_expires_at <= now:
            raise TaskIntegrityError("task approval expired before execution")
        if task.consumed_at is not None and not allow_consumed_approval:
            raise TaskIntegrityError("task approval has already been consumed")
        expected = self._digest(
            owner_id=task.owner_id,
            kind=task.kind,
            payload=task.payload,
            expires_at=task.approval_expires_at,
        )
        if task.action_digest is None or not hmac.compare_digest(expected, task.action_digest):
            raise TaskIntegrityError("task payload changed after approval")
        if task.consumed_at is None:
            task.consumed_at = now
