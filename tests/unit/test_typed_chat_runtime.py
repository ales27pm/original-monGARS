from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from mongars.autobiography.contracts import EvidenceSnapshot, StoredTurn
from mongars.config import Environment, Settings
from mongars.events.repository import ConversationMessage
from mongars.inference.base import (
    ChatMessage,
    ChatResponse,
    HealthStatus,
    InferenceResponseError,
    JsonValue,
)
from mongars.orchestrator.personality import PersonalityPreference, PersonalitySnapshot
from mongars.orchestrator.typed_chat import TypedChatRuntime
from mongars.web_search import SearchResponse, WebSearchResult


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeAutobiography:
    def __init__(
        self,
        *,
        history: tuple[StoredTurn, ...] = (),
        complete_failure: Exception | None = None,
    ) -> None:
        self.history = history
        self.complete_failure = complete_failure
        self.accepted: list[dict[str, Any]] = []
        self.started: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.failed: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.run_id = uuid4()

    async def recent_conversation(self, **_kwargs: Any) -> tuple[StoredTurn, ...]:
        return self.history

    async def accept_user_turn(self, **kwargs: Any) -> StoredTurn:
        self.accepted.append(kwargs)
        return _turn(
            role="user",
            content=str(kwargs["content"]),
            ordinal=5,
            session_id=kwargs["session_id"],
        )

    async def begin_generation(self, **kwargs: Any) -> SimpleNamespace:
        self.started.append(kwargs)
        return SimpleNamespace(id=self.run_id)

    async def complete_generation(self, **kwargs: Any) -> StoredTurn:
        if self.complete_failure is not None:
            raise self.complete_failure
        self.completed.append(kwargs)
        return _turn(
            role="assistant",
            content=str(kwargs["content"]),
            ordinal=6,
            session_id=kwargs["session_id"],
        )

    async def fail_generation(self, **kwargs: Any) -> None:
        self.failed.append(kwargs)

    async def record_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeLegacyEvents:
    def __init__(self, history: tuple[ConversationMessage, ...] = ()) -> None:
        self.history = history
        self.calls = 0

    async def recent_conversation(self, **_kwargs: Any) -> tuple[ConversationMessage, ...]:
        self.calls += 1
        return self.history


class NoMemoryRepository:
    async def has_documents(self, *, owner_id: str) -> bool:
        del owner_id
        return False


class UnusedMemory:
    async def prepare_search(self, _query: str) -> None:
        raise AssertionError("memory is disabled")

    async def search_prepared(self, **_kwargs: Any) -> list[object]:
        raise AssertionError("memory is disabled")


class FakeInference:
    def __init__(
        self,
        *,
        response: str = "Grounded answer [W1].",
        failure: BaseException | None = None,
        session: FakeSession | None = None,
    ) -> None:
        self.response = response
        self.failure = failure
        self.session = session
        self.calls: list[tuple[ChatMessage, ...]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del options
        if self.session is not None:
            assert self.session.commits >= 3
        self.calls.append(tuple(messages))
        if self.failure is not None:
            raise self.failure
        return ChatResponse(
            content=self.response,
            model=model or "deterministic-chat",
            done_reason="stop",
            prompt_tokens=12,
            completion_tokens=5,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(
            backend="fake",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


class FakeWebSearch:
    async def search(self, query: str, *, limit: int | None = None) -> SearchResponse:
        assert query
        assert limit == 5
        return SearchResponse(
            query=query,
            results=(
                WebSearchResult(
                    title="Official result",
                    url="https://example.test/result",
                    snippet="Spain won the final.",
                    engine="test",
                ),
            ),
            retrieved_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        )


def _turn(
    *,
    role: str,
    content: str,
    ordinal: int,
    session_id: UUID | None = None,
) -> StoredTurn:
    return StoredTurn(
        id=uuid4(),
        owner_id="owner",
        session_id=session_id or uuid4(),
        ordinal=ordinal,
        trace_id=f"trc_{ordinal}",
        role=role,  # type: ignore[arg-type]
        content=content,
        state="accepted" if role == "user" else "final",
        sensitivity="private",
        retention_class="keep",
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


async def _model(_owner_id: str) -> tuple[str, str | None]:
    return "qwen3:4b-instruct", "a" * 64


def _runtime(
    *,
    session: FakeSession,
    autobiography: FakeAutobiography,
    inference: FakeInference,
    legacy: FakeLegacyEvents | None = None,
    web_search: FakeWebSearch | None = None,
    personality: PersonalitySnapshot | None = None,
) -> TypedChatRuntime:
    return TypedChatRuntime(
        settings=Settings(environment=Environment.TEST, memory_top_k=0),
        inference=inference,  # type: ignore[arg-type]
        embeddings=None,
        session=session,  # type: ignore[arg-type]
        autobiography=autobiography,  # type: ignore[arg-type]
        legacy_events=legacy or FakeLegacyEvents(),  # type: ignore[arg-type]
        memory_repository=NoMemoryRepository(),  # type: ignore[arg-type]
        memory=UnusedMemory(),  # type: ignore[arg-type]
        web_search=web_search,  # type: ignore[arg-type]
        personality=personality,
        model_resolver=_model,
        utc_now=lambda: datetime(2026, 7, 24, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_web_evidence_is_keyed_snapshotted_and_completed_after_commit() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography()
    inference = FakeInference(session=session)
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=inference,
        web_search=FakeWebSearch(),
    )

    result = await runtime.chat(
        owner_id="owner",
        message="Search the web for the final result.",
        session_id=uuid4(),
        require_local_only=True,
        web_search_mode="required",
    )

    assert result.answer == "Grounded answer [W1]."
    assert [citation.key for citation in result.citations] == ["W1"]
    evidence = autobiography.started[0]["evidence"]
    assert isinstance(evidence, tuple)
    assert evidence == (
        EvidenceSnapshot(
            key="W1",
            kind="web",
            text="Spain won the final.",
            source_id=(
                "web:" + hashlib.sha256(b"https://example.test/result").hexdigest()
            ),
            title="Official result",
            source_uri="https://example.test/result",
            locator={"engine": "test", "truncated": False},
            rank=0,
            retrieved_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        ),
    )
    assert autobiography.completed[0]["citation_keys"] == ("W1",)
    tool_payload = next(
        message.content
        for message in inference.calls[0]
        if message.role == "tool" and '"kind":"web_search_results"' in message.content
    )
    assert '"key":"W1"' in tool_payload


@pytest.mark.asyncio
async def test_inference_failure_marks_generation_without_committing_assistant_turn() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography()
    inference = FakeInference(
        failure=InferenceResponseError(
            "bad response",
            backend="fake",
            operation="chat",
            retryable=True,
        ),
        session=session,
    )
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=inference,
    )

    with pytest.raises(InferenceResponseError):
        await runtime.chat(
            owner_id="owner",
            message="hello",
            session_id=uuid4(),
            require_local_only=True,
        )

    assert autobiography.failed[0]["error_code"] == "invalid_response"
    assert autobiography.failed[0]["retryable"] is True
    assert autobiography.completed == []


@pytest.mark.asyncio
async def test_cancellation_is_recorded_without_a_final_assistant_turn() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography()
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=FakeInference(failure=asyncio.CancelledError(), session=session),
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.chat(
            owner_id="owner",
            message="hello",
            session_id=uuid4(),
            require_local_only=True,
        )

    assert autobiography.completed == []
    assert autobiography.failed[0]["cancelled"] is True
    assert autobiography.failed[0]["error_code"] == "generation_cancelled"


@pytest.mark.asyncio
async def test_final_persistence_failure_transitions_the_generation_to_failed() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography(complete_failure=RuntimeError("write failed"))
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=FakeInference(response="answer", session=session),
    )

    with pytest.raises(RuntimeError, match="write failed"):
        await runtime.chat(
            owner_id="owner",
            message="hello",
            session_id=uuid4(),
            require_local_only=True,
        )

    assert autobiography.completed == []
    assert autobiography.failed[0]["error_code"] == "RuntimeError"


@pytest.mark.asyncio
async def test_legacy_prefix_and_typed_suffix_are_both_available_during_rollout() -> None:
    session = FakeSession()
    requested_session = uuid4()
    typed_history = (
        _turn(
            role="user",
            content="Correction: my shop is in Laval.",
            ordinal=1,
            session_id=requested_session,
        ),
        _turn(
            role="assistant",
            content="Understood.",
            ordinal=2,
            session_id=requested_session,
        ),
    )
    autobiography = FakeAutobiography(history=typed_history)
    legacy = FakeLegacyEvents(
        (ConversationMessage(role="user", content="My shop was in Montreal."),)
    )
    inference = FakeInference(response="You corrected it to Laval [H2].", session=session)
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=inference,
        legacy=legacy,
    )

    result = await runtime.chat(
        owner_id="owner",
        message="Where is my shop?",
        session_id=requested_session,
        require_local_only=True,
    )

    assert legacy.calls == 1
    assert [citation.key for citation in result.citations] == ["H2"]
    evidence = autobiography.started[0]["evidence"]
    assert [item.key for item in evidence] == ["H1", "H2", "H3"]
    assert evidence[0].source_id is None
    assert evidence[1].source_id == str(typed_history[0].id)


@pytest.mark.asyncio
async def test_owner_reviewed_cognitive_context_is_snapshotted_as_policy_evidence() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography()
    personality = PersonalitySnapshot(
        revision=1,
        source="approved_profile",
        preferences=(
            PersonalityPreference(
                dimension="brevity",
                value=0.9,
                confidence=1.0,
                evidence_count=1,
            ),
        ),
        profile_digest="b" * 64,
    )
    inference = FakeInference(response="I will keep this concise [P1].", session=session)
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=inference,
        personality=personality,
    )

    result = await runtime.chat(
        owner_id="owner",
        message="Explain briefly.",
        session_id=uuid4(),
        require_local_only=True,
    )

    assert [citation.key for citation in result.citations] == ["P1"]
    evidence = autobiography.started[0]["evidence"]
    assert evidence[0].kind == "policy"
    assert evidence[0].key == "P1"
    assert '"kind":"cognitive_context"' in evidence[0].text


@pytest.mark.asyncio
async def test_disabled_required_search_commits_a_policy_response_without_inference() -> None:
    session = FakeSession()
    autobiography = FakeAutobiography()
    runtime = _runtime(
        session=session,
        autobiography=autobiography,
        inference=FakeInference(
            failure=AssertionError("policy response must not invoke inference")
        ),
        web_search=None,
    )

    result = await runtime.chat(
        owner_id="owner",
        message="Search the web for current information.",
        session_id=uuid4(),
        require_local_only=True,
        web_search_mode="required",
    )

    assert result.model == "cortex-policy"
    assert result.web_search_status == "disabled"
    assert autobiography.started[0]["model_alias"] == "cortex-policy"
    assert autobiography.completed[0]["grounding_status"] == "abstained"
    assert any(
        event["event_type"] == "web_search_completed"
        and event["payload"]["status"] == "disabled"
        for event in autobiography.events
    )
