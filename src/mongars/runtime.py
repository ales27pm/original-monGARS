"""Read-side durable runtime and semantic-space readiness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import (
    MemoryChunk,
    MemoryChunkEmbedding,
    MemoryDocument,
    RuntimeComponent,
)
from mongars.embeddings.models import EmbeddingSpace

type RuntimeReportedStatus = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True, slots=True)
class RuntimeComponentSnapshot:
    """Detached representation of one durable runtime heartbeat."""

    component_id: str
    instance_id: UUID
    component_type: str
    version: str
    git_sha: str
    status: RuntimeReportedStatus
    capabilities: dict[str, Any]
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class EmbeddingCoverage:
    """Chunk coverage for one exact, immutable embedding space."""

    total_chunk_count: int
    compatible_chunk_count: int
    legacy_chunk_count: int

    @property
    def reindex_required(self) -> bool:
        return self.legacy_chunk_count > 0


@dataclass(frozen=True, slots=True)
class WorkerReadiness:
    healthy: bool
    status: str
    component_id: str | None = None
    instance_id: UUID | None = None
    version: str | None = None
    git_sha: str | None = None
    last_seen_at: datetime | None = None
    age_seconds: float | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ParserReadiness:
    healthy: bool
    version: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingSpaceReadiness:
    healthy: bool
    status: str
    active_space: EmbeddingSpace | None
    worker_space_id: str | None
    total_chunk_count: int
    compatible_chunk_count: int
    legacy_chunk_count: int
    reindex_required: bool
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DurableRuntimeReadiness:
    worker: WorkerReadiness
    parser: ParserReadiness
    embedding_space: EmbeddingSpaceReadiness

    @property
    def healthy(self) -> bool:
        return self.worker.healthy and self.parser.healthy and self.embedding_space.healthy


class RuntimeHeartbeatRepository:
    """Read durable runtime heartbeats and embedding coverage."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_worker(self) -> RuntimeComponentSnapshot | None:
        component = await self._session.scalar(
            select(RuntimeComponent)
            .where(RuntimeComponent.component_type == "worker")
            .order_by(RuntimeComponent.last_seen_at.desc())
            .limit(1)
        )
        if component is None:
            return None
        return RuntimeComponentSnapshot(
            component_id=component.component_id,
            instance_id=component.instance_id,
            component_type=component.component_type,
            version=component.version,
            git_sha=component.git_sha,
            status=cast(RuntimeReportedStatus, component.status),
            capabilities=dict(component.capabilities),
            last_seen_at=component.last_seen_at,
        )

    async def embedding_coverage(
        self,
        *,
        owner_id: str,
        space_id: str | None,
    ) -> EmbeddingCoverage:
        total_chunk_count = int(
            await self._session.scalar(
                select(func.count(MemoryChunk.id))
                .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
                .where(
                    MemoryDocument.owner_id == owner_id,
                    (
                        MemoryDocument.expires_at.is_(None)
                        | (MemoryDocument.expires_at > func.now())
                    ),
                )
            )
            or 0
        )
        compatible_chunk_count = 0
        if space_id is not None and total_chunk_count:
            compatible_chunk_count = int(
                await self._session.scalar(
                    select(func.count())
                    .select_from(MemoryChunk)
                    .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
                    .where(
                        MemoryDocument.owner_id == owner_id,
                        (
                            MemoryDocument.expires_at.is_(None)
                            | (MemoryDocument.expires_at > func.now())
                        ),
                        MemoryChunk.id.in_(
                            select(MemoryChunkEmbedding.chunk_id).where(
                                MemoryChunkEmbedding.embedding_space_id == space_id
                            )
                        ),
                    )
                )
                or 0
            )
        compatible_chunk_count = min(compatible_chunk_count, total_chunk_count)
        return EmbeddingCoverage(
            total_chunk_count=total_chunk_count,
            compatible_chunk_count=compatible_chunk_count,
            legacy_chunk_count=total_chunk_count - compatible_chunk_count,
        )


class RuntimeReadinessService:
    """Evaluate durable worker capabilities against this API runtime."""

    def __init__(self, repository: RuntimeHeartbeatRepository) -> None:
        self._repository = repository

    async def inspect(
        self,
        *,
        active_space: EmbeddingSpace | None,
        embedding_error_code: str | None,
        owner_id: str,
        stale_seconds: int,
        now: datetime | None = None,
    ) -> DurableRuntimeReadiness:
        if isinstance(stale_seconds, bool) or stale_seconds <= 0:
            raise ValueError("stale_seconds must be positive")
        if not owner_id or owner_id != owner_id.strip():
            raise ValueError("owner_id must be a non-empty trimmed string")
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        worker = await self._repository.latest_worker()
        coverage = await self._repository.embedding_coverage(
            owner_id=owner_id, space_id=active_space.space_id if active_space is not None else None
        )
        worker_status = _worker_readiness(
            worker,
            observed_at=observed_at,
            stale_seconds=stale_seconds,
        )
        parser_status = _parser_readiness(worker, worker_status=worker_status)
        embedding_status = _embedding_readiness(
            worker,
            worker_status=worker_status,
            active_space=active_space,
            embedding_error_code=embedding_error_code,
            coverage=coverage,
        )
        return DurableRuntimeReadiness(
            worker=worker_status,
            parser=parser_status,
            embedding_space=embedding_status,
        )


def unavailable_runtime_readiness(*, error_code: str) -> DurableRuntimeReadiness:
    """Return a stable unavailable shape when the read-side probe itself fails."""

    return DurableRuntimeReadiness(
        worker=WorkerReadiness(
            healthy=False,
            status="unavailable",
            error_code=error_code,
        ),
        parser=ParserReadiness(
            healthy=False,
            version=None,
            error_code="worker_unavailable",
        ),
        embedding_space=EmbeddingSpaceReadiness(
            healthy=False,
            status="unavailable",
            active_space=None,
            worker_space_id=None,
            total_chunk_count=0,
            compatible_chunk_count=0,
            legacy_chunk_count=0,
            reindex_required=False,
            error_code="runtime_unavailable",
        ),
    )


def _worker_readiness(
    worker: RuntimeComponentSnapshot | None,
    *,
    observed_at: datetime,
    stale_seconds: int,
) -> WorkerReadiness:
    if worker is None:
        return WorkerReadiness(
            healthy=False,
            status="missing",
            error_code="worker_missing",
        )
    last_seen_at = worker.last_seen_at
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    age_seconds = max(0.0, (observed_at - last_seen_at).total_seconds())
    stale = age_seconds > stale_seconds
    healthy = not stale and worker.status == "healthy"
    if stale:
        status = "stale"
        error_code = "worker_stale"
    elif worker.status != "healthy":
        status = worker.status
        error_code = f"worker_{worker.status}"
    else:
        status = "healthy"
        error_code = None
    return WorkerReadiness(
        healthy=healthy,
        status=status,
        component_id=worker.component_id,
        instance_id=worker.instance_id,
        version=worker.version,
        git_sha=worker.git_sha,
        last_seen_at=last_seen_at,
        age_seconds=age_seconds,
        error_code=error_code,
    )


def _parser_readiness(
    worker: RuntimeComponentSnapshot | None,
    *,
    worker_status: WorkerReadiness,
) -> ParserReadiness:
    if worker is None:
        return ParserReadiness(False, None, "worker_missing")
    if worker_status.status == "stale":
        return ParserReadiness(False, None, "worker_stale")
    parser = worker.capabilities.get("document_parser")
    if not isinstance(parser, dict):
        return ParserReadiness(False, None, "parser_capability_missing")
    parser_version = parser.get("parser_version")
    version = (
        parser_version
        if isinstance(parser_version, str) and parser_version.strip() == parser_version
        else None
    )
    capability_healthy = parser.get("healthy") is True
    if capability_healthy and version:
        return ParserReadiness(True, version)
    raw_error = parser.get("error_code")
    error_code = raw_error if isinstance(raw_error, str) and raw_error else "parser_unhealthy"
    return ParserReadiness(False, version, error_code)


def _embedding_readiness(
    worker: RuntimeComponentSnapshot | None,
    *,
    worker_status: WorkerReadiness,
    active_space: EmbeddingSpace | None,
    embedding_error_code: str | None,
    coverage: EmbeddingCoverage,
) -> EmbeddingSpaceReadiness:
    worker_space = worker.capabilities.get("embedding_space") if worker is not None else None
    worker_space_id = (
        worker_space.get("space_id")
        if isinstance(worker_space, dict) and isinstance(worker_space.get("space_id"), str)
        else None
    )

    status = "ready"
    error_code: str | None = None
    if active_space is None:
        status = "unresolved"
        error_code = embedding_error_code or "embedding_space_unresolved"
    elif worker is None:
        status = "worker_missing"
        error_code = "worker_missing"
    elif worker_status.status == "stale":
        status = "worker_stale"
        error_code = "worker_stale"
    elif not _worker_space_matches(worker_space, active_space):
        status = "mismatch"
        error_code = "embedding_space_mismatch"
    elif coverage.reindex_required:
        status = "reindex_required"
        error_code = "embedding_reindex_required"

    return EmbeddingSpaceReadiness(
        healthy=status == "ready",
        status=status,
        active_space=active_space,
        worker_space_id=worker_space_id,
        total_chunk_count=coverage.total_chunk_count,
        compatible_chunk_count=coverage.compatible_chunk_count,
        legacy_chunk_count=coverage.legacy_chunk_count,
        reindex_required=coverage.reindex_required,
        error_code=error_code,
    )


def _worker_space_matches(value: object, active_space: EmbeddingSpace) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        value.get("space_id") == active_space.space_id
        and value.get("model_alias") == active_space.model_alias
        and value.get("model_digest") == active_space.model_digest
        and value.get("dimension") == active_space.dimension
    )


__all__ = [
    "DurableRuntimeReadiness",
    "EmbeddingCoverage",
    "EmbeddingSpaceReadiness",
    "ParserReadiness",
    "RuntimeComponentSnapshot",
    "RuntimeHeartbeatRepository",
    "RuntimeReadinessService",
    "RuntimeReportedStatus",
    "WorkerReadiness",
    "unavailable_runtime_readiness",
]
