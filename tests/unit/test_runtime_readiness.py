from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import RuntimeComponent
from mongars.embeddings.models import EmbeddingProfile, EmbeddingSpace
from mongars.runtime import (
    EmbeddingCoverage,
    RuntimeComponentSnapshot,
    RuntimeHeartbeatRepository,
    RuntimeReadinessService,
    RuntimeReportedStatus,
)


def _space(*, digest: str = "a" * 64) -> EmbeddingSpace:
    return EmbeddingSpace.from_profile(
        provider="ollama",
        model_alias="nomic-embed-text",
        model_digest=digest,
        dimension=768,
        normalization_policy="none",
        maximum_input_bytes=32_000,
        profile=EmbeddingProfile(),
    )


def _snapshot(
    space: EmbeddingSpace,
    *,
    seen_at: datetime,
    status: str = "healthy",
    parser_healthy: bool = True,
) -> RuntimeComponentSnapshot:
    return RuntimeComponentSnapshot(
        component_id="worker:primary",
        instance_id=uuid4(),
        component_type="worker",
        version="0.1.0",
        git_sha="abc123",
        status=cast(RuntimeReportedStatus, status),
        capabilities={
            "document_parser": {
                "healthy": parser_healthy,
                "parser_version": "pypdf-6.0",
                "error_code": None if parser_healthy else "parser_offline",
            },
            "embedding_space": {
                "space_id": space.space_id,
                "model_alias": space.model_alias,
                "model_digest": space.model_digest,
                "dimension": space.dimension,
            },
        },
        last_seen_at=seen_at,
    )


class StubRuntimeRepository:
    def __init__(
        self,
        *,
        worker: RuntimeComponentSnapshot | None,
        coverage: EmbeddingCoverage,
    ) -> None:
        self.worker = worker
        self.coverage = coverage
        self.requested_owner_id: str | None = None
        self.requested_space_id: str | None = None

    async def latest_worker(self) -> RuntimeComponentSnapshot | None:
        return self.worker

    async def embedding_coverage(
        self,
        *,
        owner_id: str,
        space_id: str | None,
    ) -> EmbeddingCoverage:
        self.requested_owner_id = owner_id
        self.requested_space_id = space_id
        return self.coverage


@pytest.mark.asyncio
async def test_repository_reads_latest_worker_and_exact_space_coverage() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    component = RuntimeComponent(
        component_id="worker:primary",
        instance_id=uuid4(),
        component_type="worker",
        version="0.1.0",
        git_sha="abc123",
        status="healthy",
        capabilities={"document_parser": {"healthy": True}},
        last_seen_at=now,
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [component, 5, 3]
    repository = RuntimeHeartbeatRepository(session)

    worker = await repository.latest_worker()
    coverage = await repository.embedding_coverage(
        owner_id="owner-a",
        space_id="active-space",
    )

    assert worker is not None
    assert worker.component_id == "worker:primary"
    assert worker.capabilities == {"document_parser": {"healthy": True}}
    assert coverage == EmbeddingCoverage(
        total_chunk_count=5,
        compatible_chunk_count=3,
        legacy_chunk_count=2,
    )
    assert coverage.reindex_required is True
    assert session.scalar.await_count == 3
    for call in session.scalar.await_args_list[1:]:
        statement = call.args[0]
        compiled = statement.compile()
        sql = str(compiled)
        assert "memory_documents" in sql
        assert "memory_documents.expires_at IS NULL" in sql
        assert "memory_documents.expires_at > now()" in sql
        assert "owner-a" in compiled.params.values()


@pytest.mark.asyncio
async def test_repository_treats_empty_corpus_as_compatible_without_space_query() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.scalar.return_value = 0
    coverage = await RuntimeHeartbeatRepository(session).embedding_coverage(
        owner_id="owner-a",
        space_id="active-space",
    )

    assert coverage == EmbeddingCoverage(0, 0, 0)
    assert coverage.reindex_required is False
    session.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_accepts_fresh_matching_runtime_and_empty_corpus() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    space = _space()
    repository = StubRuntimeRepository(
        worker=_snapshot(space, seen_at=now - timedelta(seconds=5)),
        coverage=EmbeddingCoverage(0, 0, 0),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is True
    assert result.worker.healthy is True
    assert result.parser.healthy is True
    assert result.parser.version == "pypdf-6.0"
    assert result.embedding_space.healthy is True
    assert result.embedding_space.status == "ready"
    assert result.embedding_space.reindex_required is False
    assert repository.requested_owner_id == "owner-a"
    assert repository.requested_space_id == space.space_id


@pytest.mark.asyncio
async def test_service_rejects_stale_worker_and_its_capabilities() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    space = _space()
    repository = StubRuntimeRepository(
        worker=_snapshot(space, seen_at=now - timedelta(seconds=46)),
        coverage=EmbeddingCoverage(1, 1, 0),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is False
    assert result.worker.status == "stale"
    assert result.worker.error_code == "worker_stale"
    assert result.parser.error_code == "worker_stale"
    assert result.embedding_space.status == "worker_stale"


@pytest.mark.asyncio
async def test_service_rejects_missing_worker_even_for_empty_corpus() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    space = _space()
    repository = StubRuntimeRepository(
        worker=None,
        coverage=EmbeddingCoverage(0, 0, 0),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is False
    assert result.worker.status == "missing"
    assert result.worker.error_code == "worker_missing"
    assert result.parser.error_code == "worker_missing"
    assert result.embedding_space.status == "worker_missing"
    assert result.embedding_space.reindex_required is False


@pytest.mark.asyncio
async def test_service_reports_parser_failure_from_canonical_capability() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    space = _space()
    repository = StubRuntimeRepository(
        worker=_snapshot(
            space,
            seen_at=now,
            status="degraded",
            parser_healthy=False,
        ),
        coverage=EmbeddingCoverage(1, 1, 0),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is False
    assert result.worker.status == "degraded"
    assert result.parser.healthy is False
    assert result.parser.version == "pypdf-6.0"
    assert result.parser.error_code == "parser_offline"
    assert result.embedding_space.healthy is True


@pytest.mark.asyncio
async def test_service_reports_exact_space_mismatch_and_reindex_coverage() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    worker_space = _space(digest="a" * 64)
    active_space = _space(digest="b" * 64)
    repository = StubRuntimeRepository(
        worker=_snapshot(worker_space, seen_at=now),
        coverage=EmbeddingCoverage(4, 1, 3),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=active_space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is False
    assert result.embedding_space.status == "mismatch"
    assert result.embedding_space.error_code == "embedding_space_mismatch"
    assert result.embedding_space.worker_space_id == worker_space.space_id
    assert result.embedding_space.active_space == active_space
    assert result.embedding_space.compatible_chunk_count == 1
    assert result.embedding_space.legacy_chunk_count == 3
    assert result.embedding_space.reindex_required is True


@pytest.mark.asyncio
async def test_service_requires_reindex_only_for_uncovered_chunks() -> None:
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    space = _space()
    repository = StubRuntimeRepository(
        worker=_snapshot(space, seen_at=now),
        coverage=EmbeddingCoverage(4, 3, 1),
    )

    result = await RuntimeReadinessService(repository).inspect(  # type: ignore[arg-type]
        active_space=space,
        embedding_error_code=None,
        owner_id="owner-a",
        stale_seconds=45,
        now=now,
    )

    assert result.healthy is False
    assert result.embedding_space.status == "reindex_required"
    assert result.embedding_space.reindex_required is True
    assert result.embedding_space.error_code == "embedding_reindex_required"
