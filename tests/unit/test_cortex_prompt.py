from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from mongars.config import Environment, Settings
from mongars.inference import ChatMessage, ChatResponse, InferenceResponseError
from mongars.main import create_app
from mongars.memory.repository import MemoryHit
from mongars.orchestrator.cortex import (
    Cortex,
    build_prompt_envelope,
    prompt_token_upper_bound,
)
from mongars.prompting import CORTEX_MINIMUM_PROMPT_TOKENS, build_cortex_system_prompt
from mongars.web_search import SearchResponse, WebSearchError, WebSearchResult


def _memory_hit(index: int, text: str) -> MemoryHit:
    return MemoryHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        score=1.0 - (index / 100),
        text=text,
        source_uri=f"file:///memory/{index}",
        title=f"Memory {index}",
    )


def test_system_prompt_includes_the_authoritative_runtime_date_and_output_boundary() -> None:
    prompt = build_cortex_system_prompt(current_date=date(2026, 7, 22))

    assert "Current date (UTC): 2026-07-22" in prompt
    assert "authoritative over dates or knowledge cutoffs" in prompt
    assert "never expose or narrate hidden reasoning" in prompt
    assert "live verification is unavailable" in prompt


def test_completed_web_search_prompt_replaces_the_unavailable_fallback_policy() -> None:
    prompt = build_cortex_system_prompt(
        current_date=date(2026, 7, 22),
        web_search_completed=True,
    )

    assert "A live web search completed for this request" in prompt
    assert "Never say that web access or live verification is unavailable" in prompt
    assert "If a current fact requires external verification" not in prompt
    assert "say briefly that live verification is unavailable instead of guessing" not in prompt


def test_maximum_valid_user_message_is_rejected_when_it_exceeds_model_context() -> None:
    settings = Settings(
        environment=Environment.TEST,
        max_chat_chars=32_000,
        ollama_context_length=2_048,
        ollama_num_predict=512,
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
        ollama_context_length=2_048,
        ollama_num_predict=512,
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
        ollama_context_length=CORTEX_MINIMUM_PROMPT_TOKENS + 128,
        ollama_num_predict=128,
    )

    envelope = build_prompt_envelope(
        settings=settings,
        system_prompt=Cortex._SYSTEM_PROMPT,
        user_message="x",
        hits=(),
    )

    assert envelope.estimated_prompt_tokens == CORTEX_MINIMUM_PROMPT_TOKENS


class _FakeSession:
    def add(self, _value: object) -> None:
        return None

    async def flush(self) -> None:
        return None

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
        self.message_calls: list[tuple[object, ...]] = []
        self.options: object = None
        self.session = session

    async def chat(self, messages: object, **kwargs: object) -> ChatResponse:
        if self.session is not None:
            assert self.session.transaction_active is False
        self.messages = tuple(messages)  # type: ignore[arg-type]
        self.message_calls.append(self.messages)
        self.options = kwargs.get("options")
        return ChatResponse(content="bounded answer", model="test-chat")


class _StaticAnswerInference(_CapturingInference):
    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer

    async def chat(self, messages: object, **kwargs: object) -> ChatResponse:
        self.messages = tuple(messages)  # type: ignore[arg-type]
        self.message_calls.append(self.messages)
        self.options = kwargs.get("options")
        return ChatResponse(content=self.answer, model="test-chat")


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


class _SearchBackend:
    def __init__(self, session: _TrackingSession | None = None) -> None:
        self.queries: list[tuple[str, int | None]] = []
        self.session = session

    async def search(self, query: str, *, limit: int | None = None) -> SearchResponse:
        if self.session is not None:
            assert self.session.transaction_active is False
        self.queries.append((query, limit))
        return SearchResponse(
            query=query,
            results=(
                WebSearchResult(
                    title="Spain win FIFA World Cup 2026",
                    url="https://www.fifa.com/world-cup-2026-final",
                    snippet="Spain beat Argentina 1-0 after extra time.",
                    engine="test",
                ),
            ),
            retrieved_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )


class _FailingSearchBackend:
    async def search(self, _query: str, *, limit: int | None = None) -> SearchResponse:
        del limit
        raise WebSearchError("offline", code="connection_error", retryable=True)


class _UnexpectedSearchBackend:
    async def search(self, _query: str, *, limit: int | None = None) -> SearchResponse:
        del limit
        raise AssertionError("local-memory language must not trigger network search")


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
async def test_explicit_web_request_is_searched_and_serialized_as_untrusted_evidence() -> None:
    inference = _CapturingInference()
    search = _SearchBackend()
    settings = Settings(environment=Environment.TEST, memory_top_k=0)
    cortex = Cortex(
        settings=settings,
        inference=inference,  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        web_search=search,  # type: ignore[arg-type]
        utc_now=lambda: datetime(2026, 7, 22, tzinfo=UTC),
    )
    cortex._events = _EventSink()  # type: ignore[assignment]

    result = await cortex.chat(
        owner_id="owner",
        message="Search the web for the 2026 FIFA World Cup champions.",
        session_id=None,
        require_local_only=True,
    )

    assert search.queries == [("the 2026 FIFA World Cup champions.", 5)]
    assert result.web_search_status == "ok"
    assert result.sources[0].url == "https://www.fifa.com/world-cup-2026-final"
    assert len(inference.messages) == 2
    system_message = inference.messages[0]
    assert isinstance(system_message, ChatMessage)
    assert "Untrusted web-search JSON" in system_message.content
    assert (
        "trusted application code renders the supplied sources separately" in system_message.content
    )
    assert '"url":"https://www.fifa.com/world-cup-2026-final"' in system_message.content
    assert "Current date (UTC): 2026-07-22" in system_message.content
    assert inference.options == {
        "temperature": 0.0,
        "num_ctx": settings.ollama_context_length,
        "num_predict": settings.ollama_num_predict,
    }


@pytest.mark.parametrize(
    "answer",
    [
        "Live verification is unavailable, so I cannot confirm the champion.",
        "The tournament has not yet taken place, so there is no champion.",
    ],
)
@pytest.mark.asyncio
async def test_web_grounding_rejects_stale_or_refusal_answers_despite_outcome_evidence(
    answer: str,
) -> None:
    cortex = Cortex(
        settings=Settings(environment=Environment.TEST, memory_top_k=0),
        inference=_StaticAnswerInference(answer),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        web_search=_SearchBackend(),  # type: ignore[arg-type]
        utc_now=lambda: datetime(2026, 7, 22, tzinfo=UTC),
    )
    cortex._events = _EventSink()  # type: ignore[assignment]

    with pytest.raises(InferenceResponseError, match="contradicted the completed search state"):
        await cortex.chat(
            owner_id="owner",
            message="Search the web for the 2026 FIFA World Cup champions.",
            session_id=None,
            require_local_only=True,
        )


@pytest.mark.asyncio
async def test_local_memory_request_does_not_trigger_web_search() -> None:
    inference = _CapturingInference()
    cortex = Cortex(
        settings=Settings(environment=Environment.TEST, memory_top_k=0),
        inference=inference,  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        web_search=_UnexpectedSearchBackend(),  # type: ignore[arg-type]
    )
    cortex._events = _EventSink()  # type: ignore[assignment]

    result = await cortex.chat(
        owner_id="owner",
        message="Search project memory for the plan.",
        session_id=None,
        require_local_only=True,
    )

    assert result.web_search_status == "not_requested"
    assert result.sources == ()


@pytest.mark.asyncio
async def test_explicit_web_request_fails_closed_when_search_is_unavailable() -> None:
    cortex = Cortex(
        settings=Settings(environment=Environment.TEST, memory_top_k=0),
        inference=_UnusedInference(),  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        web_search=_FailingSearchBackend(),  # type: ignore[arg-type]
    )
    cortex._events = _EventSink()  # type: ignore[assignment]

    result = await cortex.chat(
        owner_id="owner",
        message="Search the web for today's result.",
        session_id=None,
        require_local_only=True,
    )

    assert result.web_search_status == "unavailable"
    assert result.model == "cortex-policy"
    assert "temporarily unavailable" in result.answer
    assert result.sources == ()


@pytest.mark.asyncio
async def test_long_lived_cortex_refreshes_the_runtime_date_on_each_turn() -> None:
    moments = iter(
        (
            datetime(2026, 7, 22, 23, 59, tzinfo=UTC),
            datetime(2026, 7, 23, 0, 1, tzinfo=UTC),
        )
    )
    inference = _CapturingInference()
    cortex = Cortex(
        settings=Settings(environment=Environment.TEST, memory_top_k=0),
        inference=inference,  # type: ignore[arg-type]
        session=_FakeSession(),  # type: ignore[arg-type]
        utc_now=lambda: next(moments),
    )
    cortex._events = _EventSink()  # type: ignore[assignment]

    for message in ("first", "second"):
        await cortex.chat(
            owner_id="owner",
            message=message,
            session_id=None,
            require_local_only=True,
        )

    first_system = inference.message_calls[0][0]
    second_system = inference.message_calls[1][0]
    assert isinstance(first_system, ChatMessage)
    assert isinstance(second_system, ChatMessage)
    assert "2026-07-22" in first_system.content
    assert "2026-07-23" in second_system.content


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
        ollama_context_length=2_048,
        ollama_num_predict=512,
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
async def test_chat_api_exposes_server_derived_web_sources() -> None:
    token = "web-source-test-token"  # noqa: S105 - test-only credential
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(token),
        memory_top_k=0,
    )
    application = create_app(
        settings=settings,
        database=_FakeDatabase(),  # type: ignore[arg-type]
        inference=_CapturingInference(),  # type: ignore[arg-type]
        web_search=_SearchBackend(),  # type: ignore[arg-type]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "Who won?", "web_search": "required"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["web_search_status"] == "ok"
    assert payload["sources"] == [
        {
            "title": "Spain win FIFA World Cup 2026",
            "url": "https://www.fifa.com/world-cup-2026-final",
        }
    ]


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
