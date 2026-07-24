from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from mongars.autobiography.contracts import StoredTurn
from mongars.events.repository import ConversationMessage
from mongars.inference.base import ChatMessage
from mongars.orchestrator.typed_evidence import key_prompt_evidence
from mongars.orchestrator.typed_journal import TypedChatJournal


class _Session:
    def __init__(self) -> None:
        self.rollbacks = 0
        self.commits = 0

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def commit(self) -> None:
        self.commits += 1


class _Autobiography:
    def __init__(self, history: tuple[StoredTurn, ...] = ()) -> None:
        self.history = history
        self.failure_started = asyncio.Event()
        self.release_failure = asyncio.Event()
        self.failure_persisted = False

    async def recent_conversation(self, **_kwargs: Any) -> tuple[StoredTurn, ...]:
        return self.history

    async def fail_generation(self, **_kwargs: Any) -> None:
        self.failure_started.set()
        await self.release_failure.wait()
        self.failure_persisted = True


class _LegacyEvents:
    def __init__(self, history: tuple[ConversationMessage, ...] = ()) -> None:
        self.history = history

    async def recent_conversation(
        self,
        **_kwargs: Any,
    ) -> tuple[ConversationMessage, ...]:
        return self.history


def _turn(*, content: str, ordinal: int, session_id: UUID) -> StoredTurn:
    return StoredTurn(
        id=uuid4(),
        owner_id="owner",
        session_id=session_id,
        ordinal=ordinal,
        trace_id=f"trc_{ordinal}",
        role="user",
        content=content,
        state="accepted",
        sensitivity="private",
        retention_class="keep",
        created_at=datetime(2026, 7, 24, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_reconstructed_duplicate_history_retains_typed_turn_attribution() -> None:
    session_id = uuid4()
    first = _turn(content="same content", ordinal=1, session_id=session_id)
    second = _turn(content="same content", ordinal=2, session_id=session_id)
    autobiography = _Autobiography((first, second))
    journal = TypedChatJournal(
        session=_Session(),  # type: ignore[arg-type]
        autobiography=autobiography,  # type: ignore[arg-type]
        legacy_events=_LegacyEvents(
            (ConversationMessage(role="assistant", content="legacy"),)
        ),  # type: ignore[arg-type]
    )

    bundle = await journal.load_history(owner_id="owner", session_id=session_id)
    reconstructed = tuple(
        ConversationMessage(role=message.role, content=message.content)
        for message in bundle.messages[-2:]
    )
    history_payload = ChatMessage(
        role="tool",
        content=json.dumps(
            {
                "kind": "conversation_history",
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in reconstructed
                ],
            }
        ),
    )

    keyed = key_prompt_evidence(
        messages=(history_payload, ChatMessage(role="user", content="question")),
        included_history=reconstructed,
        included_hits=(),
        included_web_results=(),
        history_source_ids=bundle.source_ids,
        web_retrieved_at=None,
        context_budget=100_000,
    )

    assert [item.source_id for item in keyed.evidence] == [str(first.id), str(second.id)]


@pytest.mark.asyncio
async def test_failure_persistence_completes_before_repeated_cancellation_propagates() -> None:
    session = _Session()
    autobiography = _Autobiography()
    journal = TypedChatJournal(
        session=session,  # type: ignore[arg-type]
        autobiography=autobiography,  # type: ignore[arg-type]
        legacy_events=_LegacyEvents(),  # type: ignore[arg-type]
    )
    persistence = asyncio.create_task(
        journal.persist_failure(
            owner_id="owner",
            session_id=uuid4(),
            trace_id="trc_cancelled",
            generation_run_id=uuid4(),
            error_code="generation_cancelled",
            retryable=False,
            cancelled=True,
        )
    )
    await autobiography.failure_started.wait()

    persistence.cancel()
    autobiography.release_failure.set()

    with pytest.raises(asyncio.CancelledError):
        await persistence
    assert autobiography.failure_persisted is True
    assert session.rollbacks == 1
    assert session.commits == 1
