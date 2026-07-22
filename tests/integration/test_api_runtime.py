from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from mongars.config import Environment, Settings
from mongars.db.models import (
    EpisodicEvent,
    MemoryChunk,
    MemoryChunkEmbedding,
    MemoryDocument,
    MemoryDocumentProvenance,
    RuntimeComponent,
    TaskQueue,
)
from mongars.db.session import Database
from mongars.embeddings.models import EmbeddingBatch, EmbeddingProfile, EmbeddingSpace
from mongars.embeddings.service import EmbeddingService
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    HealthStatus,
    JsonValue,
)
from mongars.ingestion.isolation import IsolatedDocumentParser
from mongars.main import create_app
from mongars.memory.chunking import TextChunk
from mongars.memory.repository import MemoryRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.runtime_heartbeat import WorkerRuntimeHeartbeat
from mongars.rm.worker import Worker

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


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> Iterator[None]:
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


class DeterministicInference:
    def __init__(self) -> None:
        self.healthy = True

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del messages, options
        return ChatResponse(content="runtime smoke answer", model=model or "deterministic-chat")

    async def health(self) -> HealthStatus:
        return HealthStatus(
            backend="ollama",
            backend_reachable=self.healthy,
            chat_model_ready=self.healthy,
            embedding_model_ready=self.healthy,
            latency_ms=0.1,
            error_code=None if self.healthy else "connection_error",
        )

    async def aclose(self) -> None:
        return None


class DeterministicEmbeddingProvider:
    provider_name = "deterministic"
    model_name = "nomic-embed-text"

    async def resolve_model_digest(self) -> str:
        return "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        vector = (1.0, *([0.0] * (expected_dimension - 1)))
        return EmbeddingBatch(
            embeddings=tuple(vector for _text in texts),
            model=self.model_name,
            model_digest=await self.resolve_model_digest(),
            dimension=expected_dimension,
            latency_ms=0.1,
        )

    async def aclose(self) -> None:
        return None


async def _clean_owner(database: Database, owner_id: str) -> None:
    async with database.session_factory() as session, session.begin():
        await session.execute(delete(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id))
        await session.execute(delete(TaskQueue).where(TaskQueue.owner_id == owner_id))
        await session.execute(delete(MemoryDocument).where(MemoryDocument.owner_id == owner_id))


@pytest.mark.asyncio
async def test_api_approval_worker_memory_and_readiness_smoke() -> None:
    owner_id = f"api-runtime-{uuid4().hex}"
    token = uuid4().hex
    settings = Settings(
        environment=Environment.TEST,
        owner_id=owner_id,
        api_token=SecretStr(token),
        approval_hmac_key=SecretStr("integration-runtime-approval-key"),
        database_url=DATABASE_URL,
        web_search_enabled=False,
        memory_chunk_tokens=64,
        memory_chunk_overlap_tokens=8,
    )
    database = Database(settings)
    inference = DeterministicInference()
    embeddings = EmbeddingService(
        provider=DeterministicEmbeddingProvider(),
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )
    parser = IsolatedDocumentParser()
    application = create_app(
        settings=settings,
        database=database,
        inference=inference,
        embeddings=embeddings,
    )
    transport = httpx.ASGITransport(app=application)
    headers = {"Authorization": f"Bearer {token}"}
    marker = f"runtime-marker-{uuid4().hex}"
    foreign_owner_id = f"api-runtime-foreign-{uuid4().hex}"

    try:
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(RuntimeComponent).where(RuntimeComponent.component_id == "worker:primary")
            )
            foreign_task = await TaskRepository(session).create(
                owner_id=foreign_owner_id,
                kind="memory.search",
                risk_level="read_only",
                # This row exists only to prove the review endpoints are owner-scoped.
                # Keep it terminal so the worker smoke below claims the task created by
                # this test instead of legitimately taking the older foreign fixture.
                status="done",
                trace_id=f"trc_{uuid4().hex}",
                payload={"query": "foreign owner data", "top_k": 8},
                action_digest=None,
                approval_expires_at=None,
            )
            foreign_task_id = foreign_task.id

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get("/v1/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            anonymous_readiness = await client.get("/v1/readyz")
            assert anonymous_readiness.status_code == 401
            invalid_readiness = await client.get(
                "/v1/readyz",
                headers={"Authorization": "Bearer invalid-runtime-token"},
            )
            assert invalid_readiness.status_code == 401

            anonymous = await client.get("/v1/tasks")
            assert anonymous.status_code == 401
            invalid = await client.get(
                "/v1/tasks",
                headers={"Authorization": "Bearer invalid-runtime-token"},
            )
            assert invalid.status_code == 401

            foreign_detail = await client.get(
                f"/v1/tasks/{foreign_task_id}",
                headers=headers,
            )
            foreign_payload = await client.get(
                f"/v1/tasks/{foreign_task_id}/payload?page=0",
                headers=headers,
            )
            assert foreign_detail.status_code == 404
            assert foreign_payload.status_code == 404

            ready = await client.get("/v1/readyz", headers=headers)
            assert ready.status_code == 503
            assert ready.json()["dependencies"]["worker"]["error_code"] == "worker_missing"

            heartbeat = WorkerRuntimeHeartbeat(
                settings=settings,
                database=database,
                embeddings=embeddings,
                document_parser=parser,
            )
            assert await heartbeat.publish_once() is True
            ready = await client.get("/v1/readyz", headers=headers)
            assert ready.status_code == 200
            dependencies = ready.json()["dependencies"]
            assert dependencies["database"] == {"healthy": True}
            assert dependencies["inference"] == {
                "backend": "ollama",
                "healthy": True,
                "backend_reachable": True,
                "chat_model_ready": True,
                "embedding_model_ready": True,
                "latency_ms": 0.1,
                "error_code": None,
            }
            assert dependencies["web_search"] == {
                "enabled": False,
                "healthy": True,
                "latency_ms": 0.0,
                "error_code": None,
            }
            assert dependencies["worker"]["healthy"] is True
            assert dependencies["worker"]["component_id"] == "worker:primary"
            assert dependencies["parser"] == {
                "healthy": True,
                "version": "local-isolated-v1",
                "error_code": None,
            }
            assert dependencies["embedding_space"]["healthy"] is True
            assert dependencies["embedding_space"]["total_chunk_count"] == 0
            assert dependencies["embedding_space"]["reindex_required"] is False

            create_response = await client.post(
                "/v1/memory/documents",
                headers=headers,
                json={
                    "text": f"The integration smoke marker is {marker}.",
                    "title": "ASGI runtime smoke",
                    "sensitivity": "private",
                    "retention_class": "ttl_30d",
                },
            )
            assert create_response.status_code == 202
            created = create_response.json()
            assert created["kind"] == "memory.note.create"
            assert created["risk_level"] == "local_mutation"
            assert created["status"] == "waiting_approval"
            task_id = UUID(created["id"])

            review_response = await client.get(f"/v1/tasks/{task_id}", headers=headers)
            assert review_response.status_code == 200
            review = review_response.json()
            assert "payload" not in review
            assert review["payload_summary"]["top_level_field_count"] == 4
            assert marker in review["payload_summary"]["preview_head"]
            assert len(review["action_digest"]) == 64

            page_response = await client.get(
                f"/v1/tasks/{task_id}/payload?page=0",
                headers=headers,
            )
            assert page_response.status_code == 200
            payload_page = page_response.json()
            assert payload_page["task_id"] == str(task_id)
            assert payload_page["action_digest"] == review["action_digest"]
            assert payload_page["page_index"] == 0
            assert marker in payload_page["content"]

            old_client_approval = await client.post(
                f"/v1/tasks/{task_id}/approve",
                headers=headers,
            )
            assert old_client_approval.status_code == 422

            stale_review_approval = await client.post(
                f"/v1/tasks/{task_id}/approve",
                headers=headers,
                json={"action_digest": "0" * 64},
            )
            assert stale_review_approval.status_code == 409

            approve_response = await client.post(
                f"/v1/tasks/{task_id}/approve",
                headers=headers,
                json={"action_digest": review["action_digest"]},
            )
            assert approve_response.status_code == 200
            assert approve_response.json()["status"] == "queued"
            assert approve_response.json()["approved_at"] is not None

            worker = Worker(
                settings=settings,
                database=database,
                inference=inference,
                embeddings=embeddings,
                document_parser=parser,
            )
            assert await worker.run_once() is True

            task_response = await client.get(f"/v1/tasks/{task_id}", headers=headers)
            assert task_response.status_code == 200
            task = task_response.json()
            assert task["status"] == "done"
            assert task["attempt_count"] == 1
            assert task["result"]["created"] is True
            assert task["result"]["chunk_count"] == 1
            document_id = UUID(task["result"]["document_id"])

            legacy_texts = (
                f"Legacy reindex marker {uuid4().hex} " + ("界" * 3_000),
                f"Second legacy marker {uuid4().hex} " + ("龍" * 3_000),
            )
            legacy_locators = (
                {
                    "kind": "pdf_page",
                    "page": 7,
                    "heading_path": ["Legacy", "Oversized"],
                },
                {
                    "kind": "pdf_page",
                    "page": 8,
                    "heading_path": ["Legacy", "Oversized"],
                },
            )
            legacy_space = EmbeddingSpace.from_profile(
                provider="deterministic",
                model_alias="legacy-nomic-tag",
                model_digest="b" * 64,
                dimension=settings.embedding_dimensions,
                normalization_policy="none",
                maximum_input_bytes=settings.embedding_max_input_bytes,
                profile=EmbeddingProfile(),
            )
            async with database.session_factory() as session, session.begin():
                legacy_document, legacy_created = await MemoryRepository(session).add_document(
                    owner_id=owner_id,
                    source_type="note",
                    source_sha256=hashlib.sha256("\n".join(legacy_texts).encode()).digest(),
                    title="Legacy embedding fixture",
                    source_uri=None,
                    mime_type="text/plain",
                    sensitivity="private",
                    retention_class="keep",
                    expires_at=None,
                    metadata={"fixture": "approved-reindex"},
                    chunks=[
                        TextChunk(
                            text=legacy_texts[index],
                            approximate_tokens=4,
                            section_path=("Legacy", "Oversized"),
                            locator=legacy_locators[index],
                        )
                        for index in range(2)
                    ],
                    embeddings=[
                        [
                            *([0.0] * index),
                            1.0,
                            *([0.0] * (settings.embedding_dimensions - index - 1)),
                        ]
                        for index in range(2)
                    ],
                    embedding_space=legacy_space,
                )
                assert legacy_created is True

            reindex_required = await client.get("/v1/readyz", headers=headers)
            assert reindex_required.status_code == 503
            assert (
                reindex_required.json()["dependencies"]["embedding_space"]["status"]
                == "reindex_required"
            )

            reindex_created = await client.post(
                "/v1/memory/reindex",
                headers=headers,
                json={"batch_size": 8},
            )
            assert reindex_created.status_code == 202
            reindex_task_id = UUID(reindex_created.json()["id"])
            reindex_review = await client.get(
                f"/v1/tasks/{reindex_task_id}",
                headers=headers,
            )
            assert reindex_review.status_code == 200
            assert reindex_review.json()["status"] == "waiting_approval"
            reindex_approved = await client.post(
                f"/v1/tasks/{reindex_task_id}/approve",
                headers=headers,
                json={"action_digest": reindex_review.json()["action_digest"]},
            )
            assert reindex_approved.status_code == 200
            assert await worker.run_once() is True
            reindex_detail = await client.get(
                f"/v1/tasks/{reindex_task_id}",
                headers=headers,
            )
            assert reindex_detail.status_code == 200
            reindex_result = reindex_detail.json()["result"]
            assert reindex_detail.json()["status"] == "done"
            assert reindex_result["reindexed_source_chunk_count"] == 2
            assert reindex_result["reindexed_chunk_count"] > 2
            assert reindex_result["compatible_chunk_count"] == (
                1 + reindex_result["reindexed_chunk_count"]
            )
            assert reindex_result["legacy_chunk_count"] == 0
            assert reindex_result["reindex_required"] is False

            ready_after_reindex = await client.get("/v1/readyz", headers=headers)
            assert ready_after_reindex.status_code == 200
            assert (
                ready_after_reindex.json()["dependencies"]["embedding_space"][
                    "compatible_chunk_count"
                ]
                == reindex_result["compatible_chunk_count"]
            )

            async with database.session_factory() as session:
                legacy_chunks = list(
                    (
                        await session.scalars(
                            select(MemoryChunk)
                            .where(MemoryChunk.document_id == legacy_document.id)
                            .order_by(MemoryChunk.chunk_index)
                        )
                    ).all()
                )
                active_vectors = list(
                    (
                        await session.scalars(
                            select(MemoryChunkEmbedding).where(
                                MemoryChunkEmbedding.chunk_id.in_(
                                    [chunk.id for chunk in legacy_chunks]
                                ),
                                MemoryChunkEmbedding.embedding_space_id
                                == reindex_result["embedding_space_id"],
                            )
                        )
                    ).all()
                )
                provenance_count = len(
                    (
                        await session.scalars(
                            select(MemoryDocumentProvenance).where(
                                MemoryDocumentProvenance.document_id == legacy_document.id
                            )
                        )
                    ).all()
                )
            assert len(legacy_chunks) == reindex_result["reindexed_chunk_count"]
            assert len(active_vectors) == len(legacy_chunks)
            assert provenance_count == 1
            assert {chunk.locator["page"] for chunk in legacy_chunks} == {7, 8}
            assert all(chunk.locator in legacy_locators for chunk in legacy_chunks)
            assert all(
                tuple(chunk.section_path) == ("Legacy", "Oversized") for chunk in legacy_chunks
            )
            for text_value, locator in zip(legacy_texts, legacy_locators, strict=True):
                located_text = "".join(
                    chunk.plaintext.replace(" ", "")
                    for chunk in legacy_chunks
                    if chunk.locator == locator
                )
                assert located_text == text_value.replace(" ", "")
            assert all(
                len((embeddings.profile.document_instruction + chunk.plaintext).encode("utf-8"))
                <= embeddings.max_text_bytes
                for chunk in legacy_chunks
            )

            document_response = await client.get(
                f"/v1/memory/documents/{document_id}",
                headers=headers,
            )
            assert document_response.status_code == 200
            document = document_response.json()
            assert document["id"] == str(document_id)
            assert document["metadata"]["task_id"] == str(task_id)

            search_response = await client.post(
                "/v1/memory/search",
                headers=headers,
                json={"query": marker, "top_k": 5, "mode": "hybrid"},
            )
            assert search_response.status_code == 200
            hits = search_response.json()["hits"]
            assert any(hit["document_id"] == str(document_id) for hit in hits)
            assert any(marker in hit["text"] for hit in hits)

            chat_response = await client.post(
                "/v1/chat",
                headers=headers,
                json={"message": "Use the stored runtime marker.", "require_local_only": True},
            )
            assert chat_response.status_code == 200
            assert chat_response.json()["answer"] == "runtime smoke answer"
            assert chat_response.json()["memory_hits"] >= 1

            list_response = await client.get("/v1/tasks", headers=headers)
            assert list_response.status_code == 200
            assert any(item["id"] == str(task_id) for item in list_response.json())

            cancelled_response = await client.post(
                "/v1/tasks",
                headers=headers,
                json={"kind": "memory.search", "payload": {"query": marker, "top_k": 1}},
            )
            assert cancelled_response.status_code == 202
            cancelled_task_id = UUID(cancelled_response.json()["id"])
            cancel_response = await client.post(
                f"/v1/tasks/{cancelled_task_id}/cancel",
                headers=headers,
            )
            assert cancel_response.status_code == 204
            cancelled_task = await client.get(
                f"/v1/tasks/{cancelled_task_id}",
                headers=headers,
            )
            assert cancelled_task.json()["status"] == "cancelled"

            inference.healthy = False
            degraded = await client.get("/v1/readyz", headers=headers)
            assert degraded.status_code == 503
            degraded_body = degraded.json()
            assert degraded_body["status"] == "not_ready"
            assert degraded_body["dependencies"]["database"]["healthy"] is True
            assert degraded_body["dependencies"]["inference"] == {
                "backend": "ollama",
                "healthy": False,
                "backend_reachable": False,
                "chat_model_ready": False,
                "embedding_model_ready": False,
                "latency_ms": 0.1,
                "error_code": "connection_error",
            }
    finally:
        await _clean_owner(database, owner_id)
        await _clean_owner(database, foreign_owner_id)
        async with database.session_factory() as session, session.begin():
            await session.execute(
                delete(RuntimeComponent).where(RuntimeComponent.component_id == "worker:primary")
            )
        await parser.aclose()
        await embeddings.aclose()
        await inference.aclose()
        await database.close()
