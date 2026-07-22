from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from mongars.db.models import MemoryDocument, TaskQueue
from mongars.memory.chunking import TextChunk
from mongars.memory.repository import MemoryRepository
from mongars.rm.repository import TaskRepository

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


async def _clean_owner_data(engine: AsyncEngine, owners: set[str]) -> None:
    async with engine.begin() as connection:
        await connection.execute(delete(TaskQueue).where(TaskQueue.owner_id.in_(owners)))
        await connection.execute(delete(MemoryDocument).where(MemoryDocument.owner_id.in_(owners)))


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
