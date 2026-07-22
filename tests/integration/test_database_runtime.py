from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from mongars.config import Environment, Settings
from mongars.db.models import (
    EpisodicEvent,
    MemoryDocument,
    MemoryDocumentProvenance,
    TaskQueue,
)
from mongars.db.session import Database
from mongars.inference.base import EmbeddingResponse
from mongars.memory.chunking import TextChunk
from mongars.memory.repository import MemoryGovernanceConflict, MemoryRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.worker import ExecutionClaim, TaskLeaseLost, Worker

_RAW_DATABASE_URL = os.getenv("MONGARS_TEST_DATABASE_URL", "").strip()
if not _RAW_DATABASE_URL:
    pytest.skip(
        "MONGARS_TEST_DATABASE_URL is required for PostgreSQL integration tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


def _psycopg_url(value: str) -> str:
    url = make_url(value)
    if url.get_backend_name() != "postgresql":
        raise ValueError("MONGARS_TEST_DATABASE_URL must target PostgreSQL")
    return url.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


DATABASE_URL = _psycopg_url(_RAW_DATABASE_URL)


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> Iterator[None]:
    """Apply checked-in migrations to the explicitly configured disposable database."""

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    previous_url = os.environ.get("MONGARS_DATABASE_URL")
    os.environ["MONGARS_DATABASE_URL"] = DATABASE_URL
    try:
        command.upgrade(config, "head")
        yield
    finally:
        if previous_url is None:
            os.environ.pop("MONGARS_DATABASE_URL", None)
        else:
            os.environ["MONGARS_DATABASE_URL"] = previous_url


def _owner(label: str) -> str:
    return f"integration-{label}-{uuid4().hex}"


def _unit_vector(index: int, *, sign: float = 1.0) -> list[float]:
    vector = [0.0] * 768
    vector[index] = sign
    return vector


def test_runtime_consistency_migration_fails_preexisting_stranded_task() -> None:
    """Upgrade an isolated 0001 database containing an unclaimable queued task."""

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    database_name = f"mongars_migration_{uuid4().hex[:12]}"
    base_url = make_url(DATABASE_URL)
    admin_url = base_url.set(
        drivername="postgresql",
        database="postgres",
    ).render_as_string(hide_password=False)
    migration_url = base_url.set(
        drivername="postgresql+psycopg",
        database=database_name,
    ).render_as_string(hide_password=False)
    direct_url = base_url.set(
        drivername="postgresql",
        database=database_name,
    ).render_as_string(hide_password=False)
    previous_url = os.environ.get("MONGARS_DATABASE_URL")
    task_id = uuid4()

    with psycopg.connect(admin_url, autocommit=True) as admin_connection:
        admin_connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )

    try:
        os.environ["MONGARS_DATABASE_URL"] = migration_url
        command.upgrade(config, "0001_initial")

        with psycopg.connect(direct_url) as connection:
            connection.execute(
                """
                INSERT INTO task_queue (
                    id,
                    owner_id,
                    kind,
                    risk_level,
                    status,
                    priority,
                    attempt_count,
                    max_attempts,
                    run_after,
                    trace_id,
                    payload
                )
                VALUES (
                    %s,
                    'migration-stranded-owner',
                    'memory.search',
                    'read_only',
                    'queued',
                    100,
                    3,
                    3,
                    now(),
                    'migration-stranded-trace',
                    '{"query": "stranded", "top_k": 1}'::jsonb
                )
                """,
                (task_id,),
            )

        command.upgrade(config, "head")

        with psycopg.connect(direct_url) as connection:
            row = connection.execute(
                """
                SELECT status, error_text, lease_expires_at, execution_token
                FROM task_queue
                WHERE id = %s
                """,
                (task_id,),
            ).fetchone()

        assert row == (
            "failed",
            "task exhausted all attempts before worker upgrade; task failed",
            None,
            None,
        )
        command.check(config)
    finally:
        if previous_url is None:
            os.environ.pop("MONGARS_DATABASE_URL", None)
        else:
            os.environ["MONGARS_DATABASE_URL"] = previous_url

        with psycopg.connect(admin_url, autocommit=True) as admin_connection:
            admin_connection.execute(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            admin_connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database_name))
            )


async def _clean_owner_data(engine: AsyncEngine, owners: set[str]) -> None:
    async with engine.begin() as connection:
        await connection.execute(delete(EpisodicEvent).where(EpisodicEvent.owner_id.in_(owners)))
        await connection.execute(delete(TaskQueue).where(TaskQueue.owner_id.in_(owners)))
        await connection.execute(delete(MemoryDocument).where(MemoryDocument.owner_id.in_(owners)))


class DeterministicEmbedding:
    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.embed_calls = 0
        self.delay_seconds = delay_seconds

    async def embed(
        self,
        inputs: list[str],
        *,
        model: str | None = None,
        expected_dimension: int | None = None,
    ) -> EmbeddingResponse:
        self.embed_calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        dimension = expected_dimension or 768
        vector = (1.0, *([0.0] * (dimension - 1)))
        return EmbeddingResponse(
            embeddings=tuple(vector for _input in inputs),
            model=model or "deterministic-embed",
            dimension=dimension,
        )


def test_migrated_schema_enforces_owner_isolation() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_a = _owner("owner-a")
        owner_b = _owner("owner-b")
        owners = {owner_a, owner_b}
        digest = hashlib.sha256(b"same source for two owners").digest()
        chunk = TextChunk(
            text="owner-scoped integration memory",
            approximate_tokens=4,
            section_path=("integration",),
        )

        try:
            async with sessions.begin() as session:
                repository = MemoryRepository(session)
                document_a, created_a = await repository.add_document(
                    owner_id=owner_a,
                    source_type="note",
                    source_sha256=digest,
                    title="Owner A",
                    source_uri=None,
                    mime_type="text/plain",
                    sensitivity="private",
                    retention_class="keep",
                    expires_at=None,
                    metadata={"test": True},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )
                document_b, created_b = await repository.add_document(
                    owner_id=owner_b,
                    source_type="note",
                    source_sha256=digest,
                    title="Owner B",
                    source_uri=None,
                    mime_type="text/plain",
                    sensitivity="private",
                    retention_class="keep",
                    expires_at=None,
                    metadata={"test": True},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )

                assert created_a is True
                assert created_b is True
                assert document_a.id != document_b.id
                assert (
                    await repository.get_document(
                        owner_id=owner_a,
                        document_id=document_a.id,
                    )
                    is document_a
                )
                assert (
                    await repository.get_document(
                        owner_id=owner_b,
                        document_id=document_a.id,
                    )
                    is None
                )
                assert (
                    await repository.find_by_digest(owner_id=owner_b, digest=digest)
                ) is document_b
        finally:
            await _clean_owner_data(engine, owners)
            await engine.dispose()

    asyncio.run(exercise())


def test_workers_skip_locked_tasks_and_complete_distinct_claims() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = _owner("queue")

        try:
            async with sessions.begin() as setup_session:
                repository = TaskRepository(setup_session)
                high_priority = await repository.create(
                    owner_id=owner_id,
                    kind="memory.search",
                    risk_level="read_only",
                    status="queued",
                    trace_id=f"trace-{uuid4().hex}",
                    payload={"query": "first", "top_k": 1},
                    action_digest=None,
                    approval_expires_at=None,
                    priority=200,
                )
                low_priority = await repository.create(
                    owner_id=owner_id,
                    kind="memory.search",
                    risk_level="read_only",
                    status="queued",
                    trace_id=f"trace-{uuid4().hex}",
                    payload={"query": "second", "top_k": 1},
                    action_digest=None,
                    approval_expires_at=None,
                    priority=100,
                )
                high_priority_id = high_priority.id
                low_priority_id = low_priority.id

            async with sessions() as worker_one, sessions() as worker_two:
                async with worker_one.begin():
                    first_repository = TaskRepository(worker_one)
                    first_claim = await first_repository.claim_next(lease_seconds=60)
                    assert first_claim is not None
                    assert first_claim.id == high_priority_id

                    async with worker_two.begin():
                        second_repository = TaskRepository(worker_two)
                        async with asyncio.timeout(2):
                            second_claim = await second_repository.claim_next(lease_seconds=60)
                        assert second_claim is not None
                        assert second_claim.id == low_priority_id
                        assert second_claim.id != first_claim.id
                        await second_repository.mark_done(
                            second_claim,
                            result={"worker": 2},
                        )

                    await first_repository.mark_done(first_claim, result={"worker": 1})

            async with sessions() as verification_session:
                tasks = list(
                    (
                        await verification_session.scalars(
                            select(TaskQueue)
                            .where(TaskQueue.owner_id == owner_id)
                            .order_by(TaskQueue.priority.desc())
                        )
                    ).all()
                )
                assert [task.id for task in tasks] == [high_priority_id, low_priority_id]
                assert [task.status for task in tasks] == ["done", "done"]
                assert [task.attempt_count for task in tasks] == [1, 1]
                assert [task.result for task in tasks] == [{"worker": 1}, {"worker": 2}]
        finally:
            await _clean_owner_data(engine, {owner_id})
            await engine.dispose()

    asyncio.run(exercise())


def test_pgvector_search_orders_deterministic_vectors_without_inference() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = _owner("vectors")
        other_owner = _owner("vectors-other")
        owners = {owner_id, other_owner}

        async def add_memory(
            repository: MemoryRepository,
            *,
            owner: str,
            label: str,
            embedding: list[float],
        ) -> None:
            await repository.add_document(
                owner_id=owner,
                source_type="note",
                source_sha256=hashlib.sha256(f"{owner}:{label}".encode()).digest(),
                title=label,
                source_uri=None,
                mime_type="text/plain",
                sensitivity="private",
                retention_class="keep",
                expires_at=None,
                metadata={"test": True},
                chunks=[
                    TextChunk(
                        text=label,
                        approximate_tokens=1,
                        section_path=("integration",),
                    )
                ],
                embeddings=[embedding],
                embedding_model="deterministic-integration",
            )

        try:
            async with sessions.begin() as session:
                repository = MemoryRepository(session)
                await add_memory(
                    repository,
                    owner=owner_id,
                    label="parallel",
                    embedding=_unit_vector(0),
                )
                await add_memory(
                    repository,
                    owner=owner_id,
                    label="orthogonal",
                    embedding=_unit_vector(1),
                )
                await add_memory(
                    repository,
                    owner=owner_id,
                    label="opposite",
                    embedding=_unit_vector(0, sign=-1.0),
                )
                await add_memory(
                    repository,
                    owner=other_owner,
                    label="other owner parallel",
                    embedding=_unit_vector(0),
                )

            async with sessions() as session:
                hits = await MemoryRepository(session).search(
                    owner_id=owner_id,
                    query_text="ignored for semantic-only search",
                    embedding=_unit_vector(0),
                    top_k=3,
                    hybrid=False,
                )

                assert [hit.text for hit in hits] == ["parallel", "orthogonal", "opposite"]
                assert [hit.score for hit in hits] == pytest.approx([1.0, 0.0, -1.0])
                assert all("other owner" not in hit.text for hit in hits)
        finally:
            await _clean_owner_data(engine, owners)
            await engine.dispose()

    asyncio.run(exercise())


def test_expired_leases_emit_distinct_terminal_and_requeue_events() -> None:
    async def exercise() -> None:
        settings = Settings(
            environment=Environment.TEST,
            owner_id=_owner("lease-recovery"),
            api_token=SecretStr("integration-token"),
            approval_hmac_key=SecretStr("integration-approval-key"),
            database_url=DATABASE_URL,
            worker_lease_seconds=10,
        )
        database = Database(settings)
        owner_id = settings.owner_id
        exhausted_id = uuid4()
        retryable_id = uuid4()
        expired_at = datetime.now(UTC) - timedelta(seconds=5)

        try:
            async with database.session_factory() as session, session.begin():
                session.add_all(
                    [
                        TaskQueue(
                            id=exhausted_id,
                            owner_id=owner_id,
                            kind="memory.search",
                            risk_level="read_only",
                            status="running",
                            priority=200,
                            attempt_count=3,
                            max_attempts=3,
                            run_after=datetime.now(UTC),
                            lease_expires_at=expired_at,
                            execution_token=uuid4(),
                            trace_id=f"trace-{uuid4().hex}",
                            payload={"query": "exhausted", "top_k": 1},
                        ),
                        TaskQueue(
                            id=retryable_id,
                            owner_id=owner_id,
                            kind="memory.search",
                            risk_level="read_only",
                            status="running",
                            priority=100,
                            attempt_count=1,
                            max_attempts=3,
                            run_after=datetime.now(UTC),
                            lease_expires_at=expired_at,
                            execution_token=uuid4(),
                            trace_id=f"trace-{uuid4().hex}",
                            payload={"query": "retryable", "top_k": 1},
                        ),
                    ]
                )

            worker = Worker(
                settings=settings,
                database=database,
                inference=DeterministicEmbedding(),  # type: ignore[arg-type]
            )
            assert await worker.run_once() is True

            async with database.session_factory() as session:
                exhausted = await session.get(TaskQueue, exhausted_id)
                retryable = await session.get(TaskQueue, retryable_id)
                assert exhausted is not None
                assert retryable is not None
                assert exhausted.status == "failed"
                assert exhausted.execution_token is None
                assert exhausted.error_text == (
                    "worker lease expired after final attempt; task failed"
                )
                assert retryable.status == "done"
                assert retryable.attempt_count == 2

                events = list(
                    (
                        await session.scalars(
                            select(EpisodicEvent).where(
                                EpisodicEvent.owner_id == owner_id,
                                EpisodicEvent.event_type.in_({"task_failed", "task_requeued"}),
                            )
                        )
                    ).all()
                )
                by_task = {event.payload["task_id"]: event for event in events}
                assert by_task[str(exhausted_id)].event_type == "task_failed"
                assert by_task[str(retryable_id)].event_type == "task_requeued"
        finally:
            await _clean_owner_data(database.engine, {owner_id})
            await database.close()

    asyncio.run(exercise())


def test_duplicate_content_preserves_provenance_and_rejects_governance_change() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = _owner("governance")
        digest = hashlib.sha256(b"same governed content").digest()
        original_expiry = datetime.now(UTC) + timedelta(days=30)
        chunk = TextChunk(text="same governed content", approximate_tokens=3)

        try:
            async with sessions.begin() as session:
                repository = MemoryRepository(session)
                original, created = await repository.add_document(
                    owner_id=owner_id,
                    source_type="note",
                    source_sha256=digest,
                    title="First source",
                    source_uri="file:///first.txt",
                    mime_type="text/plain",
                    sensitivity="restricted",
                    retention_class="ttl_30d",
                    expires_at=original_expiry,
                    metadata={"source": 1},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )
                assert created is True
                original_id = original.id

            async with sessions.begin() as session:
                duplicate, created = await MemoryRepository(session).add_document(
                    owner_id=owner_id,
                    source_type="web",
                    source_sha256=digest,
                    title="Second source",
                    source_uri="https://example.invalid/source",
                    mime_type="text/html",
                    sensitivity="restricted",
                    retention_class="ttl_30d",
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                    metadata={"source": 2},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )
                assert created is False
                assert duplicate.id == original_id
                assert duplicate.expires_at == original_expiry

            # Retrying the exact same source observation is idempotent at the
            # provenance layer as well as the content layer.
            async with sessions.begin() as session:
                retry, created = await MemoryRepository(session).add_document(
                    owner_id=owner_id,
                    source_type="web",
                    source_sha256=digest,
                    title="Second source",
                    source_uri="https://example.invalid/source",
                    mime_type="text/html",
                    sensitivity="restricted",
                    retention_class="ttl_30d",
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                    metadata={"source": 2},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )
                assert created is False
                assert retry.id == original_id

            async with sessions() as session:
                provenances = list(
                    (
                        await session.scalars(
                            select(MemoryDocumentProvenance)
                            .where(MemoryDocumentProvenance.document_id == original_id)
                            .order_by(MemoryDocumentProvenance.created_at)
                        )
                    ).all()
                )
                assert [item.source_uri for item in provenances] == [
                    "file:///first.txt",
                    "https://example.invalid/source",
                ]

            async with sessions.begin() as session:
                with pytest.raises(MemoryGovernanceConflict, match="retention_class"):
                    await MemoryRepository(session).add_document(
                        owner_id=owner_id,
                        source_type="note",
                        source_sha256=digest,
                        title="Conflicting policy",
                        source_uri=None,
                        mime_type="text/plain",
                        sensitivity="restricted",
                        retention_class="legal_hold",
                        expires_at=None,
                        metadata={},
                        chunks=[chunk],
                        embeddings=[_unit_vector(0)],
                        embedding_model="deterministic-integration",
                    )
        finally:
            await _clean_owner_data(engine, {owner_id})
            await engine.dispose()

    asyncio.run(exercise())


def test_concurrent_duplicate_with_conflicting_governance_has_typed_loser() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = _owner("governance-race")
        digest = hashlib.sha256(b"concurrent governed content").digest()
        chunk = TextChunk(text="concurrent governed content", approximate_tokens=3)

        async def ingest(retention_class: str) -> tuple[MemoryDocument, bool]:
            async with sessions.begin() as session:
                return await MemoryRepository(session).add_document(
                    owner_id=owner_id,
                    source_type="race",
                    source_sha256=digest,
                    title=retention_class,
                    source_uri=f"test://{retention_class}",
                    mime_type="text/plain",
                    sensitivity="private",
                    retention_class=retention_class,
                    expires_at=None,
                    metadata={"retention": retention_class},
                    chunks=[chunk],
                    embeddings=[_unit_vector(0)],
                    embedding_model="deterministic-integration",
                )

        try:
            outcomes = await asyncio.gather(
                ingest("keep"),
                ingest("legal_hold"),
                return_exceptions=True,
            )
            successes = [item for item in outcomes if isinstance(item, tuple)]
            conflicts = [item for item in outcomes if isinstance(item, MemoryGovernanceConflict)]
            unexpected = [
                item for item in outcomes if not isinstance(item, (tuple, MemoryGovernanceConflict))
            ]
            assert len(successes) == 1
            assert len(conflicts) == 1
            assert unexpected == []

            async with sessions() as session:
                documents = list(
                    (
                        await session.scalars(
                            select(MemoryDocument).where(MemoryDocument.owner_id == owner_id)
                        )
                    ).all()
                )
                assert len(documents) == 1
                provenance_count = len(
                    (
                        await session.scalars(
                            select(MemoryDocumentProvenance).where(
                                MemoryDocumentProvenance.document_id == documents[0].id
                            )
                        )
                    ).all()
                )
                assert provenance_count == 1
        finally:
            await _clean_owner_data(engine, {owner_id})
            await engine.dispose()

    asyncio.run(exercise())


def test_stale_execution_token_cannot_persist_memory_effect() -> None:
    async def exercise() -> None:
        settings = Settings(
            environment=Environment.TEST,
            owner_id=_owner("stale-effect"),
            api_token=SecretStr("integration-token"),
            approval_hmac_key=SecretStr("integration-approval-key"),
            database_url=DATABASE_URL,
            worker_lease_seconds=10,
        )
        database = Database(settings)
        task_id = uuid4()
        stale_token = uuid4()
        current_token = uuid4()
        content = f"must-not-persist-{uuid4().hex}"

        try:
            async with database.session_factory() as session, session.begin():
                session.add(
                    TaskQueue(
                        id=task_id,
                        owner_id=settings.owner_id,
                        kind="memory.note.create",
                        risk_level="local_mutation",
                        status="running",
                        priority=100,
                        attempt_count=1,
                        max_attempts=3,
                        run_after=datetime.now(UTC),
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                        execution_token=current_token,
                        trace_id=f"trace-{uuid4().hex}",
                        payload={
                            "text": content,
                            "title": None,
                            "sensitivity": "private",
                            "retention_class": "keep",
                        },
                        action_digest="a" * 64,
                        approval_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                        approved_at=datetime.now(UTC),
                    )
                )

            worker = Worker(
                settings=settings,
                database=database,
                inference=DeterministicEmbedding(),  # type: ignore[arg-type]
            )
            claim = ExecutionClaim(
                task_id=task_id,
                execution_token=stale_token,
                owner_id=settings.owner_id,
                kind="memory.note.create",
                trace_id=f"trace-{uuid4().hex}",
                payload={
                    "text": content,
                    "title": None,
                    "sensitivity": "private",
                    "retention_class": "keep",
                },
            )
            with pytest.raises(TaskLeaseLost, match="duplicate resolution"):
                await worker._perform_execution(claim, asyncio.Event())

            digest = hashlib.sha256(content.encode()).digest()
            async with database.session_factory() as session:
                assert (
                    await MemoryRepository(session).find_by_digest(
                        owner_id=settings.owner_id,
                        digest=digest,
                    )
                    is None
                )
                task = await session.get(TaskQueue, task_id)
                assert task is not None
                assert task.status == "running"
                assert task.execution_token == current_token
                assert task.result is None
        finally:
            await _clean_owner_data(database.engine, {settings.owner_id})
            await database.close()

    asyncio.run(exercise())


def test_expired_lease_during_embedding_rolls_back_document_and_provenance() -> None:
    async def exercise() -> None:
        settings = Settings(
            environment=Environment.TEST,
            owner_id=_owner("expired-effect"),
            api_token=SecretStr("integration-token"),
            approval_hmac_key=SecretStr("integration-approval-key"),
            database_url=DATABASE_URL,
            worker_lease_seconds=10,
        )
        database = Database(settings)
        task_id = uuid4()
        execution_token = uuid4()
        trace_id = f"trace-{uuid4().hex}"
        content = f"expire-before-effect-{uuid4().hex}"

        try:
            async with database.session_factory() as session, session.begin():
                session.add(
                    TaskQueue(
                        id=task_id,
                        owner_id=settings.owner_id,
                        kind="memory.note.create",
                        risk_level="local_mutation",
                        status="running",
                        priority=100,
                        attempt_count=1,
                        max_attempts=3,
                        run_after=datetime.now(UTC),
                        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
                        execution_token=execution_token,
                        trace_id=trace_id,
                        payload={
                            "text": content,
                            "title": None,
                            "sensitivity": "private",
                            "retention_class": "keep",
                        },
                        action_digest="b" * 64,
                        approval_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                        approved_at=datetime.now(UTC),
                    )
                )

            class ExpireLeaseEmbedding(DeterministicEmbedding):
                async def embed(
                    self,
                    inputs: list[str],
                    *,
                    model: str | None = None,
                    expected_dimension: int | None = None,
                ) -> EmbeddingResponse:
                    async with database.session_factory() as session, session.begin():
                        await session.execute(
                            update(TaskQueue)
                            .where(TaskQueue.id == task_id)
                            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                        )
                    return await super().embed(
                        inputs,
                        model=model,
                        expected_dimension=expected_dimension,
                    )

            worker = Worker(
                settings=settings,
                database=database,
                inference=ExpireLeaseEmbedding(),  # type: ignore[arg-type]
            )
            claim = ExecutionClaim(
                task_id=task_id,
                execution_token=execution_token,
                owner_id=settings.owner_id,
                kind="memory.note.create",
                trace_id=trace_id,
                payload={
                    "text": content,
                    "title": None,
                    "sensitivity": "private",
                    "retention_class": "keep",
                },
            )
            with pytest.raises(TaskLeaseLost, match="document persistence"):
                await worker._perform_execution(claim, asyncio.Event())

            async with database.session_factory() as session:
                documents = list(
                    (
                        await session.scalars(
                            select(MemoryDocument).where(
                                MemoryDocument.owner_id == settings.owner_id
                            )
                        )
                    ).all()
                )
                provenances = list(
                    (
                        await session.scalars(
                            select(MemoryDocumentProvenance)
                            .join(
                                MemoryDocument,
                                MemoryDocument.id == MemoryDocumentProvenance.document_id,
                            )
                            .where(MemoryDocument.owner_id == settings.owner_id)
                        )
                    ).all()
                )
                task = await session.get(TaskQueue, task_id)
                assert documents == []
                assert provenances == []
                assert task is not None
                assert task.status == "running"
                assert task.result is None
        finally:
            await _clean_owner_data(database.engine, {settings.owner_id})
            await database.close()

    asyncio.run(exercise())


def test_hnsw_candidate_query_plan_uses_index_with_realistic_corpus() -> None:
    async def exercise() -> None:
        engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        owner_id = _owner("hnsw-plan")
        chunk_count = 512
        chunks = [
            TextChunk(text=f"benchmark chunk {index}", approximate_tokens=3)
            for index in range(chunk_count)
        ]
        embeddings = []
        for index in range(chunk_count):
            vector = [0.0] * 768
            vector[index % 32] = 1.0
            vector[(index + 1) % 32] = 0.001 * ((index % 7) + 1)
            embeddings.append(vector)

        try:
            async with sessions.begin() as session:
                await MemoryRepository(session).add_document(
                    owner_id=owner_id,
                    source_type="benchmark",
                    source_sha256=hashlib.sha256(owner_id.encode()).digest(),
                    title="HNSW benchmark corpus",
                    source_uri=None,
                    mime_type="text/plain",
                    sensitivity="private",
                    retention_class="keep",
                    expires_at=None,
                    metadata={"benchmark": True},
                    chunks=chunks,
                    embeddings=embeddings,
                    embedding_model="deterministic-integration",
                )

            query_vector = "[" + ",".join(str(value) for value in _unit_vector(0)) + "]"
            async with sessions.begin() as session:
                await session.execute(text("SET LOCAL enable_seqscan = off"))
                await session.execute(text("SET LOCAL enable_sort = off"))
                plan_rows = (
                    await session.execute(
                        text(
                            "EXPLAIN (ANALYZE, BUFFERS) "
                            "SELECT mc.id, mc.embedding <=> CAST(:embedding AS vector) AS distance "
                            "FROM memory_chunks AS mc "
                            "JOIN memory_documents AS md ON md.id = mc.document_id "
                            "WHERE md.owner_id = :owner_id "
                            "ORDER BY mc.embedding <=> CAST(:embedding AS vector) ASC "
                            "LIMIT 32"
                        ),
                        {"embedding": query_vector, "owner_id": owner_id},
                    )
                ).all()
                plan = "\n".join(str(row[0]) for row in plan_rows)
                assert "ix_memory_chunks_embedding_hnsw" in plan
                assert "Buffers:" in plan
        finally:
            await _clean_owner_data(engine, {owner_id})
            await engine.dispose()

    asyncio.run(exercise())
