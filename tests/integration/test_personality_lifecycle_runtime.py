from __future__ import annotations

import json
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

from mongars.adaptation.models import (
    ExplicitFeedbackRecord,
    PersonalityProfileLifecycleRecord,
    PersonalityProfileRecord,
    PersonalityProfileRevisionRecord,
)
from mongars.config import Environment, Settings
from mongars.db.models import EpisodicEvent, TaskQueue
from mongars.db.session import Database
from mongars.embeddings.models import EmbeddingBatch
from mongars.embeddings.service import EmbeddingService
from mongars.inference import ChatMessage, ChatResponse, JsonValue
from mongars.ingestion.isolation import IsolatedDocumentParser
from mongars.main import create_app
from mongars.rm.adaptation_worker import AdaptationWorker

_RAW_DATABASE_URL = os.getenv("MONGARS_TEST_DATABASE_URL", "").strip()
if not _RAW_DATABASE_URL:
    pytest.skip(
        "MONGARS_TEST_DATABASE_URL is required for PostgreSQL integration tests",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_PRIVATE_CORRECTION = "Keep this private correction in the authenticated export only."


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


class CapturingInference:
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del messages, options
        return ChatResponse(content="lifecycle answer", model=model or "deterministic-chat")

    async def aclose(self) -> None:
        return None


class UnusedEmbeddingProvider:
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
        await session.execute(
            delete(PersonalityProfileLifecycleRecord).where(
                PersonalityProfileLifecycleRecord.owner_id == owner_id
            )
        )
        await session.execute(
            delete(PersonalityProfileRevisionRecord).where(
                PersonalityProfileRevisionRecord.owner_id == owner_id
            )
        )
        await session.execute(
            delete(PersonalityProfileRecord).where(PersonalityProfileRecord.owner_id == owner_id)
        )
        await session.execute(
            delete(ExplicitFeedbackRecord).where(ExplicitFeedbackRecord.owner_id == owner_id)
        )
        await session.execute(delete(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id))
        await session.execute(delete(TaskQueue).where(TaskQueue.owner_id == owner_id))


async def _approve(
    client: httpx.AsyncClient,
    *,
    headers: dict[str, str],
    task_id: UUID,
) -> None:
    detail = await client.get(f"/v1/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200
    action_digest = detail.json()["action_digest"]
    assert isinstance(action_digest, str) and len(action_digest) == 64
    approval = await client.post(
        f"/v1/tasks/{task_id}/approve",
        headers=headers,
        json={"action_digest": action_digest},
    )
    assert approval.status_code == 200
    assert approval.json()["status"] == "queued"


async def _run_task(
    worker: AdaptationWorker,
    database: Database,
    task_id: UUID,
) -> None:
    async with database.session_factory() as session, session.begin():
        task = await session.get(TaskQueue, task_id, with_for_update=True)
        assert task is not None
        task.priority = 1_000
    worker._next_retention_sweep = float("inf")
    assert await worker.run_once() is True


@pytest.mark.asyncio
async def test_export_reset_and_privacy_delete_flow() -> None:
    owner_id = f"personality-lifecycle-{uuid4().hex}"
    token = uuid4().hex
    settings = Settings(
        environment=Environment.TEST,
        owner_id=owner_id,
        api_token=SecretStr(token),
        approval_hmac_key=SecretStr("personality-lifecycle-approval-key"),
        database_url=DATABASE_URL,
        memory_top_k=0,
        web_search_enabled=False,
    )
    database = Database(settings)
    inference = CapturingInference()
    embeddings = EmbeddingService(
        provider=UnusedEmbeddingProvider(),
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )
    parser = IsolatedDocumentParser()
    app = create_app(
        settings=settings,
        database=database,
        inference=inference,  # type: ignore[arg-type]
        embeddings=embeddings,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    worker = AdaptationWorker(
        settings=settings,
        database=database,
        inference=inference,  # type: ignore[arg-type]
        embeddings=embeddings,
        document_parser=parser,
    )

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            assert (await client.get("/v1/adaptation/profile/export")).status_code == 401
            assert (await client.post("/v1/adaptation/profile/reset")).status_code == 401
            assert (await client.post("/v1/adaptation/profile/delete")).status_code == 401

            initial_chat = await client.post(
                "/v1/chat",
                headers=headers,
                json={"message": "Give me a response to correct."},
            )
            assert initial_chat.status_code == 200
            trace_id = initial_chat.json()["trace_id"]

            correction = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "correction",
                    "feedback_id": str(uuid4()),
                    "response_trace_id": trace_id,
                    "correction_text": _PRIVATE_CORRECTION,
                },
            )
            assert correction.status_code == 202

            preference = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "preference",
                    "feedback_id": str(uuid4()),
                    "dimension": "technical_depth",
                    "desired_value": 0.9,
                },
            )
            assert preference.status_code == 202
            apply_task_id = UUID(preference.json()["proposal_task"]["id"])
            await _approve(client, headers=headers, task_id=apply_task_id)

        await _run_task(worker, database, apply_task_id)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            exported = await client.get("/v1/adaptation/profile/export", headers=headers)
            assert exported.status_code == 200
            assert "attachment;" in exported.headers["content-disposition"]
            export_payload = exported.json()
            assert export_payload["schema_version"] == "mongars-personality-export-v1"
            assert export_payload["profile"]["revision"] == 1
            assert len(export_payload["revisions"]) == 1
            serialized_export = json.dumps(export_payload)
            assert _PRIVATE_CORRECTION in serialized_export
            assert '"desired_value": 0.9' in serialized_export

            reset = await client.post("/v1/adaptation/profile/reset", headers=headers)
            assert reset.status_code == 202
            reset_task_id = UUID(reset.json()["id"])
            assert reset.json()["kind"] == "personality.profile.reset"
            assert reset.json()["status"] == "waiting_approval"

            duplicate_reset = await client.post("/v1/adaptation/profile/reset", headers=headers)
            assert duplicate_reset.status_code == 202
            assert duplicate_reset.json()["id"] == str(reset_task_id)
            await _approve(client, headers=headers, task_id=reset_task_id)

        await _run_task(worker, database, reset_task_id)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            profile = await client.get("/v1/adaptation/profile", headers=headers)
            assert profile.status_code == 200
            assert profile.json()["revision"] == 2
            assert profile.json()["source"] == "approved_profile"
            assert profile.json()["preferences"] == []

            lifecycle = await client.get(
                "/v1/adaptation/profile/lifecycle",
                headers=headers,
            )
            assert lifecycle.status_code == 200
            assert lifecycle.json()[0]["operation"] == "reset"
            assert lifecycle.json()[0]["target_revision"] == 2
            assert lifecycle.json()[0]["data_state_digest"] is None

            stale_delete_request = await client.post(
                "/v1/adaptation/profile/delete",
                headers=headers,
            )
            assert stale_delete_request.status_code == 202
            stale_delete_task_id = UUID(stale_delete_request.json()["id"])
            await _approve(client, headers=headers, task_id=stale_delete_task_id)

            late_correction = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "correction",
                    "feedback_id": str(uuid4()),
                    "response_trace_id": trace_id,
                    "correction_text": "This arrived after deletion review.",
                },
            )
            assert late_correction.status_code == 202

        await _run_task(worker, database, stale_delete_task_id)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            stale_delete = await client.get(
                f"/v1/tasks/{stale_delete_task_id}",
                headers=headers,
            )
            assert stale_delete.status_code == 200
            assert stale_delete.json()["status"] == "failed"
            assert "changed after deletion review" in stale_delete.json()["error_text"]

            delete_request = await client.post(
                "/v1/adaptation/profile/delete",
                headers=headers,
            )
            assert delete_request.status_code == 202
            delete_task_id = UUID(delete_request.json()["id"])
            assert delete_request.json()["kind"] == "personality.profile.delete"
            await _approve(client, headers=headers, task_id=delete_task_id)

        await _run_task(worker, database, delete_task_id)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            completed = await client.get(f"/v1/tasks/{delete_task_id}", headers=headers)
            assert completed.status_code == 200
            assert completed.json()["status"] == "done"
            assert completed.json()["result"]["profile_revision"] == 0
            assert completed.json()["result"]["deleted_feedback"] == 3
            assert completed.json()["result"]["deleted_revisions"] == 1
            assert completed.json()["result"]["deleted_tasks"] >= 3

            removed_apply = await client.get(f"/v1/tasks/{apply_task_id}", headers=headers)
            removed_reset = await client.get(f"/v1/tasks/{reset_task_id}", headers=headers)
            assert removed_apply.status_code == 404
            assert removed_reset.status_code == 404

            profile = await client.get("/v1/adaptation/profile", headers=headers)
            assert profile.json() == {
                "revision": 0,
                "source": "default",
                "profile_digest": None,
                "preferences": [],
            }
            revisions = await client.get(
                "/v1/adaptation/profile/revisions",
                headers=headers,
            )
            assert revisions.json() == []

            exported = await client.get("/v1/adaptation/profile/export", headers=headers)
            export_payload = exported.json()
            assert export_payload["profile"]["revision"] == 0
            assert export_payload["feedback"] == []
            assert export_payload["revisions"] == []
            assert len(export_payload["lifecycle_events"]) == 1
            receipt = export_payload["lifecycle_events"][0]
            assert receipt["operation"] == "delete"
            assert isinstance(receipt["data_state_digest"], str)
            assert len(receipt["data_state_digest"]) == 64
            assert _PRIVATE_CORRECTION not in json.dumps(export_payload)

        async with database.session_factory() as session, session.begin():
            events = list(
                (
                    await session.scalars(
                        select(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id)
                    )
                ).all()
            )
            serialized_events = json.dumps([event.payload for event in events], default=str)
            assert _PRIVATE_CORRECTION not in serialized_events
            assert "desired_value" not in serialized_events
    finally:
        await _clean_owner(database, owner_id)
        await parser.aclose()
        await embeddings.aclose()
        await inference.aclose()
        await database.close()
