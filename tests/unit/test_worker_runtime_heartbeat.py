from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import Executable

from mongars.config import Settings
from mongars.db.session import Database
from mongars.embeddings.models import EmbeddingBatch, EmbeddingProfile, EmbeddingSpace
from mongars.embeddings.service import EmbeddingService
from mongars.ingestion.isolation import DocumentParser, ParserHealth
from mongars.rm.runtime_heartbeat import WorkerRuntimeHeartbeat


def _space() -> EmbeddingSpace:
    return EmbeddingSpace.from_profile(
        provider="ollama",
        model_alias="nomic-embed-text",
        model_digest="a" * 64,
        dimension=768,
        normalization_policy="none",
        maximum_input_bytes=32_000,
        profile=EmbeddingProfile(),
    )


class CapturingSession:
    def __init__(self, database: CapturingDatabase) -> None:
        self._database = database

    async def execute(self, statement: Executable) -> None:
        assert self._database.transaction_active is True
        if self._database.publish_error is not None:
            raise self._database.publish_error
        self._database.statements.append(statement)
        self._database.published.put_nowait(None)


class CapturingSessionFactory:
    def __init__(self, database: CapturingDatabase) -> None:
        self._database = database

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[CapturingSession]:
        assert self._database.transaction_active is False
        self._database.transaction_active = True
        try:
            yield CapturingSession(self._database)
        finally:
            self._database.transaction_active = False


class CapturingDatabase:
    def __init__(self, *, publish_error: Exception | None = None) -> None:
        self.transaction_active = False
        self.publish_error = publish_error
        self.statements: list[Executable] = []
        self.published: asyncio.Queue[None] = asyncio.Queue()
        self.session_factory = CapturingSessionFactory(self)


class ProbeParser:
    def __init__(
        self,
        database: CapturingDatabase,
        *,
        health: ParserHealth | None = None,
        error: Exception | None = None,
    ) -> None:
        self._database = database
        self._health = health or ParserHealth(True, "mongars-parser-v1")
        self._error = error
        self.calls = 0

    async def health(self) -> ParserHealth:
        assert self._database.transaction_active is False
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._health


class ProbeEmbeddings:
    def __init__(
        self,
        database: CapturingDatabase,
        *,
        space: EmbeddingSpace | None = None,
        error: Exception | None = None,
        embed_error: Exception | None = None,
    ) -> None:
        self._database = database
        self._space = space or _space()
        self._error = error
        self._embed_error = embed_error
        self.resolve_calls = 0
        self.embed_calls = 0

    async def resolve_space(self) -> EmbeddingSpace:
        assert self._database.transaction_active is False
        self.resolve_calls += 1
        if self._error is not None:
            raise self._error
        return self._space

    async def embed(
        self,
        texts: list[str],
        *,
        purpose: str,
    ) -> EmbeddingBatch:
        assert self._database.transaction_active is False
        self.embed_calls += 1
        if self._embed_error is not None:
            raise self._embed_error
        assert texts == ["monGARS embedding readiness probe"]
        assert purpose == "classification"
        vector = (1.0, *([0.0] * (self._space.dimension - 1)))
        return EmbeddingBatch(
            embeddings=(vector,),
            model=self._space.model_alias,
            model_digest=self._space.model_digest,
            dimension=self._space.dimension,
            latency_ms=0.1,
            embedding_space_id=self._space.space_id,
            purpose="classification",
        )


def _heartbeat(
    database: CapturingDatabase,
    *,
    parser: ProbeParser | None = None,
    embeddings: ProbeEmbeddings | None = None,
    interval: float = 10.0,
) -> WorkerRuntimeHeartbeat:
    settings = Settings(
        worker_runtime_heartbeat_seconds=interval,
        worker_runtime_stale_seconds=max(10, int(interval * 3) + 1),
        runtime_version="1.1.0-test",
        runtime_git_sha="abc123",
    )
    return WorkerRuntimeHeartbeat(
        settings=settings,
        database=cast(Database, database),
        embeddings=cast(EmbeddingService, embeddings or ProbeEmbeddings(database)),
        document_parser=cast(DocumentParser, parser or ProbeParser(database)),
        instance_id=UUID("12345678-1234-5678-1234-567812345678"),
    )


@pytest.mark.asyncio
async def test_heartbeat_probes_before_transaction_and_upserts_canonical_capabilities() -> None:
    database = CapturingDatabase()
    parser = ProbeParser(database)
    embeddings = ProbeEmbeddings(database)
    heartbeat = _heartbeat(database, parser=parser, embeddings=embeddings)

    assert await heartbeat.publish_once() is True

    assert parser.calls == 1
    assert embeddings.resolve_calls == 1
    assert embeddings.embed_calls == 1
    assert database.transaction_active is False
    assert len(database.statements) == 1
    compiled = database.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    parameters = compiled.params
    assert "ON CONFLICT (component_id) DO UPDATE" in sql
    assert parameters["component_id"] == "worker:primary"
    assert parameters["instance_id"] == UUID("12345678-1234-5678-1234-567812345678")
    assert parameters["component_type"] == "worker"
    assert parameters["version"] == "1.1.0-test"
    assert parameters["git_sha"] == "abc123"
    assert parameters["status"] == "healthy"
    assert parameters["capabilities"] == {
        "document_parser": {
            "healthy": True,
            "parser_version": "mongars-parser-v1",
            "error_code": None,
        },
        "embedding_space": {
            "space_id": _space().space_id,
            "model_alias": "nomic-embed-text",
            "model_digest": "a" * 64,
            "dimension": 768,
        },
        "evolution_scheduler": {
            "enabled": False,
            "status": "disabled",
            "reason": "disabled_by_default",
            "can_run": False,
            "budgets": {
                "cpu_percent": 30,
                "memory_megabytes": 2048,
                "wall_clock_seconds": 45,
                "database_row_budget": 500,
                "proposal_count_budget": 25,
                "storage_bytes": 20_000_000,
                "proposal_cooldown_minutes": 30,
                "allow_network": False,
            },
        },
        "model_governance": {
            "enabled": False,
            "status": "disabled",
            "healthy": True,
            "reason": "disabled_by_default",
            "candidate_registry": {
                "active_alias": None,
                "active_digest": None,
                "active_generation": None,
                "prior_generation_anchor": None,
                "rollback_target_alias": None,
                "rollback_target_digest": None,
            },
            "benchmarks": {
                "scoring_policy_version": None,
                "benchmarking_policy_version": None,
                "minimum_sample_size": None,
                "promotion_quality_threshold": None,
                "rollback_quality_threshold": None,
            },
        },
    }


@pytest.mark.asyncio
async def test_probe_failures_publish_degraded_shape_without_leaking_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = CapturingDatabase()
    parser = ProbeParser(database, error=RuntimeError("parser secret token"))
    embeddings = ProbeEmbeddings(database, error=RuntimeError("embedding secret token"))
    heartbeat = _heartbeat(database, parser=parser, embeddings=embeddings)

    assert await heartbeat.publish_once() is True

    compiled = database.statements[0].compile(dialect=postgresql.dialect())
    assert compiled.params["status"] == "degraded"
    assert compiled.params["capabilities"] == {
        "document_parser": {
            "healthy": False,
            "parser_version": None,
            "error_code": "parser_probe_failed",
        },
        "embedding_space": {
            "space_id": None,
            "model_alias": None,
            "model_digest": None,
            "dimension": None,
        },
        "evolution_scheduler": {
            "enabled": False,
            "status": "disabled",
            "reason": "disabled_by_default",
            "can_run": False,
            "budgets": {
                "cpu_percent": 30,
                "memory_megabytes": 2048,
                "wall_clock_seconds": 45,
                "database_row_budget": 500,
                "proposal_count_budget": 25,
                "storage_bytes": 20_000_000,
                "proposal_cooldown_minutes": 30,
                "allow_network": False,
            },
        },
        "model_governance": {
            "enabled": False,
            "status": "disabled",
            "healthy": True,
            "reason": "disabled_by_default",
            "candidate_registry": {
                "active_alias": None,
                "active_digest": None,
                "active_generation": None,
                "prior_generation_anchor": None,
                "rollback_target_alias": None,
                "rollback_target_digest": None,
            },
            "benchmarks": {
                "scoring_policy_version": None,
                "benchmarking_policy_version": None,
                "minimum_sample_size": None,
                "promotion_quality_threshold": None,
                "rollback_quality_threshold": None,
            },
        },
    }
    assert "secret token" not in caplog.text
    assert {getattr(record, "error_type", None) for record in caplog.records} == {"RuntimeError"}


@pytest.mark.asyncio
async def test_real_embedding_probe_failure_publishes_degraded_readiness() -> None:
    database = CapturingDatabase()
    embeddings = ProbeEmbeddings(
        database,
        embed_error=RuntimeError("provider response contained private detail"),
    )
    heartbeat = _heartbeat(database, embeddings=embeddings)

    assert await heartbeat.publish_once() is True

    compiled = database.statements[0].compile(dialect=postgresql.dialect())
    assert compiled.params["status"] == "degraded"
    assert compiled.params["capabilities"]["embedding_space"] == {
        "space_id": None,
        "model_alias": None,
        "model_digest": None,
        "dimension": None,
    }
    assert embeddings.resolve_calls == 1
    assert embeddings.embed_calls == 1


@pytest.mark.asyncio
async def test_publish_failure_is_nonfatal_and_does_not_log_connection_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = CapturingDatabase(
        publish_error=RuntimeError("postgresql://user:secret@database/mongars")
    )
    heartbeat = _heartbeat(database)

    assert await heartbeat.publish_once() is False

    assert database.transaction_active is False
    assert database.statements == []
    assert "postgresql://" not in caplog.text
    assert "secret" not in caplog.text
    assert [getattr(record, "error_type", None) for record in caplog.records] == ["RuntimeError"]


@pytest.mark.asyncio
async def test_heartbeat_runs_immediately_and_at_configured_interval() -> None:
    database = CapturingDatabase()
    parser = ProbeParser(database)
    embeddings = ProbeEmbeddings(database)
    heartbeat = _heartbeat(
        database,
        parser=parser,
        embeddings=embeddings,
        interval=0.01,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(heartbeat.run(stop))
    try:
        async with asyncio.timeout(1):
            await database.published.get()
            await database.published.get()
    finally:
        stop.set()
        await task

    assert len(database.statements) >= 2
    assert parser.calls == len(database.statements)
    assert embeddings.resolve_calls == len(database.statements)
    assert embeddings.embed_calls == len(database.statements)
