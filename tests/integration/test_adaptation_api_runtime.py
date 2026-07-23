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
_PRIVATE_CORRECTION = "This correction must remain outside autobiographical event payloads."
_MISSING_TRACE_ID = "trc_" + ("a" * 32)


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
    def __init__(self) -> None:
        self.message_calls: list[tuple[ChatMessage, ...]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del options
        self.message_calls.append(tuple(messages))
        return ChatResponse(content="profile-aware answer", model=model or "deterministic-chat")

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
            delete(PersonalityProfileRevisionRecord).where(
                PersonalityProfileRevisionRecord.owner_id == owner_id
            )
        )
        await session.execute(
            delete(PersonalityProfileRecord).where(
                PersonalityProfileRecord.owner_id == owner_id
            )
        )
        await session.execute(
            delete(ExplicitFeedbackRecord).where(
                ExplicitFeedbackRecord.owner_id == owner_id
            )
        )
        await session.execute(delete(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id))
        await session.execute(delete(TaskQueue).where(TaskQueue.owner_id == owner_id))


@pytest.mark.asyncio
async def test_feedback_task_worker_and_chat_profile_flow() -> None:
    owner_id = f"adaptation-api-{uuid4().hex}"
    token = uuid4().hex
    settings = Settings(
        environment=Environment.TEST,
        owner_id=owner_id,
        api_token=SecretStr(token),
        approval_hmac_key=SecretStr("adaptation-api-approval-key"),
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
        inference=inference,
        embeddings=embeddings,
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    feedback_id = uuid4()

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            anonymous = await client.get("/v1/adaptation/profile")
            assert anonymous.status_code == 401

            profile = await client.get("/v1/adaptation/profile", headers=headers)
            assert profile.status_code == 200
            assert profile.json() == {
                "revision": 0,
                "source": "default",
                "profile_digest": None,
                "preferences": [],
            }

            missing_trace = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "correction",
                    "feedback_id": str(uuid4()),
                    "response_trace_id": _MISSING_TRACE_ID,
                    "correction_text": _PRIVATE_CORRECTION,
                },
            )
            assert missing_trace.status_code == 404

            initial_chat = await client.post(
                "/v1/chat",
                headers=headers,
                json={"message": "Give me an initial response."},
            )
            assert initial_chat.status_code == 200
            response_trace_id = initial_chat.json()["trace_id"]

            correction = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "correction",
                    "feedback_id": str(uuid4()),
                    "response_trace_id": response_trace_id,
                    "correction_text": _PRIVATE_CORRECTION,
                },
            )
            assert correction.status_code == 202
            assert correction.json()["proposal_task"] is None
            assert correction.json()["profile"]["revision"] == 0

            submission = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "preference",
                    "feedback_id": str(feedback_id),
                    "dimension": "technical_depth",
                    "desired_value": 0.85,
                },
            )
            assert submission.status_code == 202
            accepted = submission.json()
            assert accepted["created"] is True
            assert accepted["profile"]["revision"] == 0
            assert accepted["proposal_task"]["kind"] == "personality.profile.apply"
            assert accepted["proposal_task"]["risk_level"] == "local_mutation"
            assert accepted["proposal_task"]["status"] == "waiting_approval"
            task_id = UUID(accepted["proposal_task"]["id"])

            duplicate = await client.post(
                "/v1/adaptation/feedback",
                headers=headers,
                json={
                    "kind": "preference",
                    "feedback_id": str(feedback_id),
                    "dimension": "technical_depth",
                    "desired_value": 0.85,
                },
            )
            assert duplicate.status_code == 202
            assert duplicate.json()["created"] is False
            assert duplicate.json()["proposal_task"]["id"] == str(task_id)

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

        async with database.session_factory() as session, session.begin():
            task = await session.get(TaskQueue, task_id, with_for_update=True)
            assert task is not None
            task.priority = 1_000

        worker = AdaptationWorker(
            settings=settings,
            database=database,
            inference=inference,  # type: ignore[arg-type]
            embeddings=embeddings,
            document_parser=parser,
        )
        worker._next_retention_sweep = float("inf")
        assert await worker.run_once() is True

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            completed = await client.get(f"/v1/tasks/{task_id}", headers=headers)
            assert completed.status_code == 200
            assert completed.json()["status"] == "done"
            assert completed.json()["result"]["profile_revision"] == 1

            profile = await client.get("/v1/adaptation/profile", headers=headers)
            assert profile.status_code == 200
            current = profile.json()
            assert current["revision"] == 1
            assert current["source"] == "explicit_feedback"
            assert current["preferences"] == [
                {
                    "dimension": "technical_depth",
                    "value": 0.85,
                    "confidence": 1.0,
                    "evidence_count": 1,
                }
            ]

            revisions = await client.get(
                "/v1/adaptation/profile/revisions",
                headers=headers,
            )
            assert revisions.status_code == 200
            assert len(revisions.json()) == 1
            assert revisions.json()[0]["task_id"] == str(task_id)
            assert revisions.json()[0]["changed_dimension"] == "technical_depth"

            chat = await client.post(
                "/v1/chat",
                headers=headers,
                json={"message": "Explain the result."},
            )
            assert chat.status_code == 200
            assert chat.json()["answer"] == "profile-aware answer"

        tool_payloads = [
            json.loads(message.content)
            for message in inference.message_calls[-1]
            if message.role == "tool"
        ]
        cognitive = next(payload for payload in tool_payloads if payload["kind"] == "cognitive_context")
        assert cognitive["personality"]["revision"] == 1
        assert cognitive["personality"]["preferences"][0]["dimension"] == "technical_depth"

        async with database.session_factory() as session, session.begin():
            events = list(
                (
                    await session.scalars(
                        select(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id)
                    )
                ).all()
            )
            serialized_events = json.dumps([event.payload for event in events], default=str)
            assert "desired_value" not in serialized_events
            assert _PRIVATE_CORRECTION not in serialized_events
    finally:
        await _clean_owner(database, owner_id)
        await parser.aclose()
        await embeddings.aclose()
        await inference.aclose()
        await database.close()
