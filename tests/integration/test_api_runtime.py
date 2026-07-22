from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete
from sqlalchemy.engine import make_url

from mongars.config import Environment, Settings
from mongars.db.models import EpisodicEvent, MemoryDocument, TaskQueue
from mongars.db.session import Database
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    EmbeddingResponse,
    HealthStatus,
    JsonValue,
)
from mongars.main import create_app
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

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        model: str | None = None,
        expected_dimension: int | None = None,
    ) -> EmbeddingResponse:
        dimension = expected_dimension or 768
        vector = (1.0, *([0.0] * (dimension - 1)))
        return EmbeddingResponse(
            embeddings=tuple(vector for _input in inputs),
            model=model or "deterministic-embed",
            dimension=dimension,
        )

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
    application = create_app(settings=settings, database=database, inference=inference)
    transport = httpx.ASGITransport(app=application)
    headers = {"Authorization": f"Bearer {token}"}
    marker = f"runtime-marker-{uuid4().hex}"

    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            health = await client.get("/v1/healthz")
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}

            anonymous = await client.get("/v1/tasks")
            assert anonymous.status_code == 401
            invalid = await client.get(
                "/v1/tasks",
                headers={"Authorization": "Bearer invalid-runtime-token"},
            )
            assert invalid.status_code == 401

            ready = await client.get("/v1/readyz")
            assert ready.status_code == 200
            assert ready.json()["dependencies"] == {
                "database": {"healthy": True},
                "inference": {
                    "backend": "ollama",
                    "healthy": True,
                    "backend_reachable": True,
                    "chat_model_ready": True,
                    "embedding_model_ready": True,
                    "latency_ms": 0.1,
                    "error_code": None,
                },
                "web_search": {
                    "enabled": False,
                    "healthy": True,
                    "latency_ms": 0.0,
                    "error_code": None,
                },
            }

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
            assert review["payload"] == {
                "text": f"The integration smoke marker is {marker}.",
                "title": "ASGI runtime smoke",
                "sensitivity": "private",
                "retention_class": "ttl_30d",
            }
            assert len(review["action_digest"]) == 64

            approve_response = await client.post(
                f"/v1/tasks/{task_id}/approve",
                headers=headers,
            )
            assert approve_response.status_code == 200
            assert approve_response.json()["status"] == "queued"
            assert approve_response.json()["approved_at"] is not None

            worker = Worker(settings=settings, database=database, inference=inference)
            assert await worker.run_once() is True

            task_response = await client.get(f"/v1/tasks/{task_id}", headers=headers)
            assert task_response.status_code == 200
            task = task_response.json()
            assert task["status"] == "done"
            assert task["attempt_count"] == 1
            assert task["result"]["created"] is True
            assert task["result"]["chunk_count"] == 1
            document_id = UUID(task["result"]["document_id"])

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
            degraded = await client.get("/v1/readyz")
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
        await inference.aclose()
        await database.close()
