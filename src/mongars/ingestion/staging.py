from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import DocumentStaging, TaskQueue


@dataclass(frozen=True, slots=True)
class ExpiredStagingTask:
    task_id: UUID
    owner_id: str
    trace_id: str


class DocumentStagingRepository:
    """Owner-scoped persistence for bounded upload bytes awaiting approval."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        staging_id: UUID,
        task_id: UUID,
        owner_id: str,
        original_filename: str,
        detected_mime_type: str,
        source_sha256: bytes,
        content: bytes,
        source_timestamp: datetime,
        expires_at: datetime,
        max_owner_objects: int,
        max_owner_bytes: int,
    ) -> DocumentStaging:
        if not content:
            raise ValueError("staged document content must not be empty")
        if len(source_sha256) != 32:
            raise ValueError("staged document digest must contain 32 bytes")
        if max_owner_objects <= 0 or max_owner_bytes <= 0:
            raise ValueError("staging quotas must be positive")

        # Serialize quota accounting for one owner so concurrent multipart uploads
        # cannot race the aggregate PostgreSQL storage ceiling.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:owner_id, 0))"),
            {"owner_id": owner_id},
        )
        count, total_bytes = (
            await self._session.execute(
                select(
                    func.count(DocumentStaging.id),
                    func.coalesce(func.sum(DocumentStaging.byte_size), 0),
                ).where(DocumentStaging.owner_id == owner_id)
            )
        ).one()
        if int(count) >= max_owner_objects:
            raise DocumentStagingQuotaError("active document staging object limit reached")
        if int(total_bytes) + len(content) > max_owner_bytes:
            raise DocumentStagingQuotaError("active document staging byte limit reached")
        staged = DocumentStaging(
            id=staging_id,
            task_id=task_id,
            owner_id=owner_id,
            original_filename=original_filename,
            detected_mime_type=detected_mime_type,
            source_sha256=source_sha256,
            byte_size=len(content),
            content=bytes(content),
            source_timestamp=source_timestamp,
            expires_at=expires_at,
        )
        self._session.add(staged)
        await self._session.flush()
        return staged

    async def get_for_owner(
        self,
        *,
        staging_id: UUID,
        owner_id: str,
        for_update: bool = False,
    ) -> DocumentStaging | None:
        statement = select(DocumentStaging).where(
            DocumentStaging.id == staging_id,
            DocumentStaging.owner_id == owner_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(DocumentStaging | None, await self._session.scalar(statement))

    async def get_for_execution(
        self,
        *,
        staging_id: UUID,
        owner_id: str,
        task_id: UUID,
        for_update: bool = False,
    ) -> DocumentStaging | None:
        statement = select(DocumentStaging).where(
            DocumentStaging.id == staging_id,
            DocumentStaging.owner_id == owner_id,
            DocumentStaging.task_id == task_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(DocumentStaging | None, await self._session.scalar(statement))

    async def delete_for_execution(
        self,
        *,
        staging_id: UUID,
        owner_id: str,
        task_id: UUID,
    ) -> bool:
        statement = delete(DocumentStaging).where(
            DocumentStaging.id == staging_id,
            DocumentStaging.owner_id == owner_id,
            DocumentStaging.task_id == task_id,
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return bool(result.rowcount)

    async def delete_for_task(self, *, owner_id: str, task_id: UUID) -> bool:
        statement = delete(DocumentStaging).where(
            DocumentStaging.owner_id == owner_id,
            DocumentStaging.task_id == task_id,
        )
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return bool(result.rowcount)

    async def cleanup_stale(self, *, owner_id: str) -> tuple[ExpiredStagingTask, ...]:
        """Remove terminal stages and cancel expired unapproved uploads.

        Queued and running tasks deliberately retain their bytes even when the staging
        TTL passes; once approved, task ownership and lease semantics control cleanup.
        """

        now = datetime.now(UTC)
        statement = (
            select(DocumentStaging, TaskQueue)
            .join(TaskQueue, TaskQueue.id == DocumentStaging.task_id)
            .where(
                DocumentStaging.owner_id == owner_id,
                (
                    TaskQueue.status.in_(("done", "failed", "cancelled"))
                    | (
                        (TaskQueue.status == "waiting_approval")
                        & (
                            (DocumentStaging.expires_at <= now)
                            | (TaskQueue.approval_expires_at <= now)
                        )
                    )
                ),
            )
            .order_by(DocumentStaging.created_at, DocumentStaging.id)
            .with_for_update(skip_locked=True)
        )
        rows = (await self._session.execute(statement)).all()
        expired: list[ExpiredStagingTask] = []
        for staged, task in rows:
            if task.status == "waiting_approval":
                task.status = "cancelled"
                task.error_text = "document upload approval expired"
                task.updated_at = now
                expired.append(
                    ExpiredStagingTask(
                        task_id=task.id,
                        owner_id=task.owner_id,
                        trace_id=task.trace_id,
                    )
                )
            await self._session.delete(staged)
        await self._session.flush()
        return tuple(expired)


class DocumentStagingQuotaError(ValueError):
    """Raised when an owner would exceed a serialized staging quota."""


__all__ = [
    "DocumentStagingQuotaError",
    "DocumentStagingRepository",
    "ExpiredStagingTask",
]
