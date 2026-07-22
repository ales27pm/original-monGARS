from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import EpisodicEvent


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One owner- and session-scoped prior chat message."""

    role: Literal["user", "assistant"]
    content: str


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        owner_id: str,
        trace_id: str,
        actor: str,
        event_type: str,
        summary: str,
        session_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> EpisodicEvent:
        event = EpisodicEvent(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            actor=actor,
            event_type=event_type,
            summary=summary,
            payload=payload or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def recent_conversation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        limit: int,
    ) -> tuple[ConversationMessage, ...]:
        """Return recent chat messages in chronological order within one session."""

        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("conversation history limit must be between 1 and 100")
        statement = (
            select(EpisodicEvent)
            .where(
                EpisodicEvent.owner_id == owner_id,
                EpisodicEvent.session_id == session_id,
                EpisodicEvent.event_type == "message",
                EpisodicEvent.actor.in_(("user", "cortex")),
            )
            .order_by(EpisodicEvent.created_at.desc(), EpisodicEvent.id.desc())
            .limit(limit)
        )
        events = (await self._session.scalars(statement)).all()
        messages: list[ConversationMessage] = []
        for event in reversed(events):
            stored_content = event.payload.get("content")
            content = stored_content if isinstance(stored_content, str) else event.summary
            if not content.strip():
                continue
            messages.append(
                ConversationMessage(
                    role="user" if event.actor == "user" else "assistant",
                    content=content,
                )
            )
        return tuple(messages)
