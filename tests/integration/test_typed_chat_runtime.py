from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from mongars.autobiography.tables import (
    AutobiographicalEventRecord,
    ConversationTurn,
    GenerationEvidence,
    GenerationRun,
)
from mongars.config import Environment, Settings
from mongars.db.models import EpisodicEvent
from mongars.db.session import Database
from mongars.embeddings.models import EmbeddingBatch
from mongars.embeddings.service import EmbeddingService
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    HealthStatus,
    InferenceResponseError,
    JsonValue,
)
from mongars.main import create_app

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
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del messages, options
        return ChatResponse(
            content="typed autobiographical answer",
            model=model or "deterministic-chat",
            done_reason="stop",
            prompt_tokens=11,
            completion_tokens=4,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(
            backend="deterministic",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


class FailingInference(DeterministicInference):
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del messages, model, options
        raise InferenceResponseError(
            "private malformed response detail",
            backend="deterministic",
            operation="chat",
            retryable=True,
        )


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
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


def _settings(*, owner_id: str, token: str) -> Settings:
    return Settings(
        environment=Environment.TEST,
        owner_id=owner_id,
        api_token=SecretStr(token),
        approval_hmac_key=SecretStr("typed-chat-integration-approval-key"),
        database_url=DATABASE_URL,
        memory_top_k=0,
        web_search_enabled=False,
    )


def _embeddings(settings: Settings) -> EmbeddingService:
    return EmbeddingService(
        provider=UnusedEmbeddingProvider(),
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )


async def _clean_owner(database: Database, owner_id: str) -> None:
    async with database.session_factory() as session, session.begin():
        run_ids = select(GenerationRun.id).where(GenerationRun.owner_id == owner_id)
        await session.execute(
            delete(GenerationEvidence).where(GenerationEvidence.generation_run_id.in_(run_ids))
        )
        await session.execute(
            delete(AutobiographicalEventRecord).where(
                AutobiographicalEventRecord.owner_id == owner_id
            )
        )
        await session.execute(delete(GenerationRun).where(GenerationRun.owner_id == owner_id))
        await session.execute(
            delete(ConversationTurn).where(ConversationTurn.owner_id == owner_id)
        )
        await session.execute(delete(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id))


@pytest.mark.asyncio
async def test_chat_persists_typed_turn_generation_and_events() -> None:
    owner_id = f"typed-chat-{uuid4().hex}"
    token = uuid4().hex
    settings = _settings(owner_id=owner_id, token=token)
    database = Database(settings)
    inference = DeterministicInference()
    embeddings = _embeddings(settings)
    application = create_app(
        settings=settings,
        database=database,
        inference=inference,
        embeddings=embeddings,
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/v1/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": "Record this typed conversation.",
                    "require_local_only": True,
                    "web_search": "off",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"] == "typed autobiographical answer"
        assert payload["citations"] == []

        async with database.session_factory() as session:
            turns = (
                await session.scalars(
                    select(ConversationTurn)
                    .where(ConversationTurn.owner_id == owner_id)
                    .order_by(ConversationTurn.ordinal)
                )
            ).all()
            runs = (
                await session.scalars(
                    select(GenerationRun).where(GenerationRun.owner_id == owner_id)
                )
            ).all()
            events = (
                await session.scalars(
                    select(AutobiographicalEventRecord)
                    .where(AutobiographicalEventRecord.owner_id == owner_id)
                    .order_by(
                        AutobiographicalEventRecord.occurred_at,
                        AutobiographicalEventRecord.id,
                    )
                )
            ).all()
            evidence = (
                await session.scalars(
                    select(GenerationEvidence)
                    .join(GenerationRun, GenerationRun.id == GenerationEvidence.generation_run_id)
                    .where(GenerationRun.owner_id == owner_id)
                )
            ).all()
            legacy_events = (
                await session.scalars(
                    select(EpisodicEvent).where(EpisodicEvent.owner_id == owner_id)
                )
            ).all()

        assert [(turn.role, turn.state) for turn in turns] == [
            ("user", "accepted"),
            ("assistant", "final"),
        ]
        assert len(runs) == 1
        assert runs[0].status == "completed"
        assert runs[0].user_turn_id == turns[0].id
        assert runs[0].assistant_turn_id == turns[1].id
        assert runs[0].prompt_tokens == 11
        assert runs[0].completion_tokens == 4
        assert len(runs[0].prompt_sha256) == 32
        assert evidence == []
        assert legacy_events == []
        assert [event.event_type for event in events] == [
            "session_started",
            "user_turn_accepted",
            "generation_started",
            "retrieval_completed",
            "generation_completed",
            "assistant_turn_committed",
        ]
    finally:
        await _clean_owner(database, owner_id)
        await embeddings.aclose()
        await inference.aclose()
        await database.close()


@pytest.mark.asyncio
async def test_failed_chat_records_generation_without_final_assistant_turn() -> None:
    owner_id = f"typed-chat-failure-{uuid4().hex}"
    token = uuid4().hex
    settings = _settings(owner_id=owner_id, token=token)
    database = Database(settings)
    inference = FailingInference()
    embeddings = _embeddings(settings)
    application = create_app(
        settings=settings,
        database=database,
        inference=inference,
        embeddings=embeddings,
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/v1/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "Fail safely.", "web_search": "off"},
            )

        assert response.status_code == 503
        assert response.json() == {
            "detail": {"code": "invalid_response", "retryable": True}
        }
        assert "private malformed response detail" not in response.text

        async with database.session_factory() as session:
            turns = (
                await session.scalars(
                    select(ConversationTurn)
                    .where(ConversationTurn.owner_id == owner_id)
                    .order_by(ConversationTurn.ordinal)
                )
            ).all()
            run = await session.scalar(
                select(GenerationRun).where(GenerationRun.owner_id == owner_id)
            )
            events = (
                await session.scalars(
                    select(AutobiographicalEventRecord)
                    .where(AutobiographicalEventRecord.owner_id == owner_id)
                    .order_by(
                        AutobiographicalEventRecord.occurred_at,
                        AutobiographicalEventRecord.id,
                    )
                )
            ).all()

        assert [(turn.role, turn.state) for turn in turns] == [("user", "accepted")]
        assert run is not None
        assert run.status == "failed"
        assert run.error_code == "invalid_response"
        assert run.assistant_turn_id is None
        assert [event.event_type for event in events] == [
            "session_started",
            "user_turn_accepted",
            "generation_started",
            "retrieval_completed",
            "generation_failed",
        ]
    finally:
        await _clean_owner(database, owner_id)
        await embeddings.aclose()
        await inference.aclose()
        await database.close()
