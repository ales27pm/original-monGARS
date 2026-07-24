from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url

from mongars.api.chat_streaming import StreamingBouche
from mongars.autobiography.tables import (
    AutobiographicalEventRecord,
    ConversationTurn,
    GenerationEvidence,
    GenerationRun,
)
from mongars.config import Environment, Settings
from mongars.db.models import EpisodicEvent
from mongars.db.session import Database
from mongars.dialogue import DialoguePlan
from mongars.embeddings.models import EmbeddingBatch
from mongars.embeddings.service import EmbeddingService
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    HealthStatus,
    JsonValue,
)
from mongars.main import create_app
from mongars.orchestrator.typed_chat import TypedChatRuntime

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


class StreamingInference:
    async def chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del options
        return ChatResponse(content="fallback answer", model=model or "deterministic-chat")

    async def stream_chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        del options
        selected = model or "deterministic-chat"
        yield ChatStreamChunk(content="streamed ", model=selected)
        yield ChatStreamChunk(
            content="answer",
            model=selected,
            done=True,
            done_reason="stop",
            prompt_tokens=9,
            completion_tokens=2,
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


class BlockingStreamingInference(StreamingInference):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream_chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        del options
        self.started.set()
        await self.release.wait()
        yield ChatStreamChunk(
            content="must not commit",
            model=model or "deterministic-chat",
            done=True,
            done_reason="stop",
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
        approval_hmac_key=SecretStr("streaming-chat-integration-approval-key"),
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


async def _noop_start(_plan: DialoguePlan) -> None:
    return None


async def _noop_delta(_text: str) -> None:
    return None


@pytest.mark.asyncio
async def test_authenticated_ndjson_stream_commits_one_final_assistant_turn() -> None:
    owner_id = f"typed-stream-{uuid4().hex}"
    token = uuid4().hex
    settings = _settings(owner_id=owner_id, token=token)
    database = Database(settings)
    inference = StreamingInference()
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
                "/v1/chat/stream",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "Stream this safely.", "web_search": "off"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        frames = [json.loads(line) for line in response.text.splitlines() if line]
        assert [frame["type"] for frame in frames] == [
            "start",
            "sources",
            "delta",
            "final",
        ]
        assert "".join(
            frame["text"] for frame in frames if frame["type"] == "delta"
        ) == "streamed answer"
        assert frames[-1]["answer"] == "streamed answer"
        assert frames[-1]["citations"] == []
        assert frames[-1]["trace_id"] == frames[0]["trace_id"]
        assert frames[-1]["session_id"] == frames[0]["session_id"]

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

        assert [(turn.role, turn.state) for turn in turns] == [
            ("user", "accepted"),
            ("assistant", "final"),
        ]
        assert turns[-1].content == "streamed answer"
        assert run is not None
        assert run.status == "completed"
        assert run.assistant_turn_id == turns[-1].id
        assert run.prompt_tokens == 9
        assert run.completion_tokens == 2
    finally:
        await _clean_owner(database, owner_id)
        await embeddings.aclose()
        await inference.aclose()
        await database.close()


@pytest.mark.asyncio
async def test_cancelled_stream_records_cancelled_run_without_assistant_turn() -> None:
    owner_id = f"typed-stream-cancel-{uuid4().hex}"
    token = uuid4().hex
    settings = _settings(owner_id=owner_id, token=token)
    database = Database(settings)
    inference = BlockingStreamingInference()
    embeddings = _embeddings(settings)

    try:
        async with database.session_factory() as session:
            runtime = TypedChatRuntime(
                settings=settings,
                inference=inference,
                embeddings=embeddings,
                session=session,
                bouche=StreamingBouche(
                    inference,
                    on_start=_noop_start,
                    on_delta=_noop_delta,
                ),
            )
            task = asyncio.create_task(
                runtime.chat(
                    owner_id=owner_id,
                    message="Cancel after the generation begins.",
                    session_id=None,
                    require_local_only=True,
                    web_search_mode="off",
                )
            )
            await asyncio.wait_for(inference.started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

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
        assert run.status == "cancelled"
        assert run.error_code == "generation_cancelled"
        assert run.assistant_turn_id is None
        assert [event.event_type for event in events][-1] == "generation_cancelled"
    finally:
        inference.release.set()
        await _clean_owner(database, owner_id)
        await embeddings.aclose()
        await inference.aclose()
        await database.close()
