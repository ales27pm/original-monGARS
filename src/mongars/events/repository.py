from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import EpisodicEvent


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
