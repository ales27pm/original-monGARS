from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from mongars.config import Environment, Settings
from mongars.inference import ChatResponse
from mongars.main import create_app
from mongars.memory.repository import MemoryHit
from mongars.orchestrator.cortex import (
    Cortex,
    build_prompt_envelope,
    prompt_token_upper_bound,
)
from mongars.prompting import CORTEX_MINIMUM_PROMPT_TOKENS


def _memory_hit(index: int, text: str) -> MemoryHit:
    return MemoryHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        score=1.0 - (index / 100),
        text=text,
        source_uri=f"file:///memory/{index}",
        title=f"Memory {index}",
    )


def test_maximum_valid_user_message_is_rejected_when_it_exceeds_model_context() -> None:
    settings = Settings(
        environment=Environment.TEST,
        max_chat_chars=32_000,
        ollama_context_length=512,
        ollama_num_predict=128,
    )

    with pytest.raises(ValueError, match="model context budget"):
        build_prompt_envelope(
            settings=settings,
            system_prompt=Cortex._SYSTEM_PROMPT,
            user_message="x" * settings.max_chat_chars,
            hits=(),
        )


def test_retrieval_is_truncated_to_the_remaining_prompt_budget() -> None:
    settings = Settings(
        environment=Environment.TEST,
        ollama_context_length=1_024,
        ollama_num_predict=128,
    )
    hits = [_memory_hit(index, "retrieved text " * 1_000) for index in range(8)]

    envelope = build_prompt_envelope(
        settings=settings,
        system_prompt=Cortex._SYSTEM_PROMPT,
        user_message="Use relevant memory.",
        hits=hits,
    )

    assert envelope.messages[0].content == Cortex._SYSTEM_PROMPT
    assert envelope.messages[-1].content == "Use relevant memory."
    assert 0 < len(envelope.included_hits) < len(hits)
    assert envelope.included_hits[0] is hits[0]
    assert '"truncated":true' in envelope.messages[1].content
    assert envelope.estimated_prompt_tokens == prompt_token_upper_bound(envelope.messages)
    assert envelope.estimated_prompt_tokens <= (
        settings.ollama_context_length - settings.ollama_num_predict
    )


def test_exact_minimum_prompt_budget_accepts_smallest_valid_message() -> None:
    settings = Settings(
        environment=Environment.TEST,
        ollama_context_length=512,
        ollama_num_predict=512 - CORTEX_MINIMUM_PROMPT_TOKENS,
    )

    envelope = build_prompt_envelope(
        settings=settings,
        system_prompt=Cortex._SYSTEM_PROMPT,
        user_message="x",
        hits=(),
    )

    assert envelope.estimated_prompt_tokens == CORTEX_MINIMUM_PROMPT_TOKENS


class _FakeSession:
    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _TrackingSession(_FakeSession):
    def __init__(self) -> None:
        self.transaction_active = False
        self.commits = 0

    async def commit(self) -> None:
        self.transaction_active = False
        self.commits += 1


class _FakeDatabase:
    @asynccontextmanager
    async def session_factory(self) -> Any:
        yield _FakeSession()

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _UnusedInference:
    async def chat(self, *_args: object, **_kwargs: object) -> ChatResponse:
        raise AssertionError("oversized prompt must not reach inference")

    async def aclose(self) -> None:
        return None


class _CapturingInference(_UnusedInference):
    def __init__(self, session: _TrackingSession | None = None) -> None:
        self.messages: tuple[object, ...] = ()
        self.options: object = None
        self.session = session

    async def chat(self, messages: object, **kwargs: object) -> ChatResponse:
        if self.session is not None:
            assert self.session.transaction_active is False
        self.messages = tuple(messages)  # type: ignore[arg-type]
        self.options = kwargs.get("options")
        return ChatResponse(content="bounded answer", model="test-chat")


class _EventSink:
    def __init__(self, session: _TrackingSession | None = None) -> None:
        self.session = session

    async def record(self, **_kwargs: object) -> None:
        if self.session is not None:
            self.session.transaction_active = True
        return None


class _UnexpectedEventSink:
    async def record(self, **_kwargs: object) -> None:
        raise AssertionError("local-only rejection must happen before event persistence")


class _NoMemoryRepository:
    async def has_documents(self, *, owner_id: str) -> bool:
        del owner_id
        return False


class _HasMemoryRepository:
    def __init__(self, session: _TrackingSession) -> None:
        self.session = session

    async def has_documents(self, *, owner_id: str) -> bool:
        del owner_id
        assert self.session.transaction_active is False
        self.session.transaction_active = True
        return True


class _PhasedMemory:
    def __init__(self, session: _TrackingSession) -> None:
        self.session = session

    async def prepare_search(self, query: str) -> str:
        assert query
        assert self.session.transaction_active is False
        return query

    async def search_prepared(self, **_kwargs: object) -> list[MemoryHit]:
        assert self.session.transaction_active is False
        self.session.transaction_active = True
        return []


@pytest.mark.asyncio
async def test_cortex_passes_the_reviewed_context_and_completion_limits() -> None:
    settings = Settings(
        environment=Environment.TEST,
        memory_top_k=8,
        ollama_context_length=2_048,
        ollama_num_predict=384,
    )
    inference = _CapturingInference()
    cortex = Cortex(
        settings=settings,
        inference=inference,  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
    )
    cortex._events = _EventSink()  # type: ignore[assignment]
    cortex._memory_repository = _NoMemoryRepository()  # type: ignore[assignment]

    result = await cortex.chat(
        owner_id="owner",
        message="hello",
        session_id=None,
        require_local_only=True,
    )

    assert result.answer == "bounded answer"
    assert inference.options == {
        "temperature": 0.2,
        "num_ctx": 2_048,
        "num_predict": 384,
    }
    assert inference.messages


@pytest.mark.asyncio
async def test_cortex_rejects_remote_ollama_for_local_only_request_before_side_effects() -> None:
    settings = Settings(
        environment=Environment.TEST,
        ollama_base_url="https://gpu-box.example:11434",
        allow_remote_inference=True,
    )
    cortex = Cortex(
        settings=settings,
        inference=_UnusedInference(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
    )
    cortex._events = _UnexpectedEventSink()  # type: ignore[assignment]

    with pytest.raises(PermissionError, match="local inference endpoint"):
        await cortex.chat(
            owner_id="owner",
            message="private message",
            session_id=None,
            require_local_only=True,
        )


@pytest.mark.asyncio
async def test_cortex_ends_database_phases_before_external_inference() -> None:
    settings = Settings(environment=Environment.TEST)
    session = _TrackingSession()
    inference = _CapturingInference(session)
    cortex = Cortex(
        settings=settings,
        inference=inference,  # type: ignore[arg-type]
        session=session,  # type: ignore[arg-type]
    )
    cortex._events = _EventSink(session)  # type: ignore[assignment]
    cortex._memory_repository = _HasMemoryRepository(session)  # type: ignore[assignment]
    cortex._memory = _PhasedMemory(session)  # type: ignore[assignment]

    result = await cortex.chat(
        owner_id="owner",
        message="hello",
        session_id=None,
        require_local_only=True,
    )

    assert result.answer == "bounded answer"
    assert session.transaction_active is False
    assert session.commits == 4


@pytest.mark.asyncio
async def test_chat_api_maps_context_budget_failure_to_422() -> None:
    token = "prompt-budget-test-token"  # noqa: S105 - test-only credential
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(token),
        ollama_context_length=512,
        ollama_num_predict=128,
    )
    application = create_app(
        settings=settings,
        database=_FakeDatabase(),  # type: ignore[arg-type]
        inference=_UnusedInference(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "x" * settings.max_chat_chars},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "message exceeds the configured model context budget"}


@pytest.mark.asyncio
async def test_chat_api_rejects_remote_ollama_when_request_requires_local_only() -> None:
    token = "local-only-test-token"  # noqa: S105 - test-only credential
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(token),
        ollama_base_url="https://gpu-box.example:11434",
        allow_remote_inference=True,
    )
    application = create_app(
        settings=settings,
        database=_FakeDatabase(),  # type: ignore[arg-type]
        inference=_UnusedInference(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "private message", "require_local_only": True},
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "a local inference endpoint is required"}
