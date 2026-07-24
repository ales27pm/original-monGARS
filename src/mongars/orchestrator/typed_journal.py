"""Short-transaction journal operations for typed chat orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mongars.autobiography.contracts import EvidenceSnapshot, GroundingStatus, StoredTurn
from mongars.autobiography.service import AutobiographyService
from mongars.autobiography.tables import GenerationRun
from mongars.dialogue import ComposedResponse, DialoguePlan
from mongars.events.repository import ConversationMessage, EventRepository
from mongars.orchestrator.cortex import WebSearchStatus

logger = logging.getLogger(__name__)
_SESSION_HISTORY_LIMIT = 12


@dataclass(frozen=True, slots=True)
class HistoryBundle:
    messages: tuple[ConversationMessage, ...]
    source_ids: dict[int, str]


class TypedChatJournal:
    """Persist chat lifecycle records without owning retrieval or inference."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        autobiography: AutobiographyService | None = None,
        legacy_events: EventRepository | None = None,
    ) -> None:
        self._session = session
        self._autobiography = autobiography or AutobiographyService(session)
        self._legacy_events = legacy_events or EventRepository(session)

    async def load_history(self, *, owner_id: str, session_id: UUID) -> HistoryBundle:
        typed_turns = await self._autobiography.recent_conversation(
            owner_id=owner_id,
            session_id=session_id,
            limit=_SESSION_HISTORY_LIMIT,
        )
        legacy = await self._legacy_events.recent_conversation(
            owner_id=owner_id,
            session_id=session_id,
            limit=_SESSION_HISTORY_LIMIT,
        )
        pairs: list[tuple[ConversationMessage, StoredTurn]] = []
        for turn in typed_turns:
            if turn.role not in {"user", "assistant"}:
                continue
            message = ConversationMessage(
                role="user" if turn.role == "user" else "assistant",
                content=turn.content,
            )
            pairs.append((message, turn))

        # Generic episodic messages are the pre-migration prefix; typed turns are the
        # newer suffix. Keep both until an explicit history backfill removes this seam.
        typed_messages = tuple(message for message, _turn in pairs)
        combined = (*legacy, *typed_messages)[-_SESSION_HISTORY_LIMIT:]
        retained_ids = {id(message) for message in combined}
        source_ids = {
            id(message): str(turn.id)
            for message, turn in pairs
            if id(message) in retained_ids
        }
        return HistoryBundle(messages=tuple(combined), source_ids=source_ids)

    async def accept_user_turn(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        content: str,
        new_session: bool,
    ) -> StoredTurn:
        if new_session:
            await self._autobiography.record_event(
                owner_id=owner_id,
                session_id=session_id,
                trace_id=trace_id,
                event_type="session_started",
                actor_type="cortex",
                payload={"session_id": session_id},
            )
        return await self._autobiography.accept_user_turn(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            content=content,
        )

    async def begin_generation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        user_turn_id: UUID,
        plan: DialoguePlan,
        prompt_bytes: bytes,
        grounding_status: GroundingStatus,
    ) -> GenerationRun:
        return await self._autobiography.begin_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            user_turn_id=user_turn_id,
            model_alias=plan.model_alias,
            model_digest=plan.model_digest,
            prompt_recipe_version=plan.prompt_recipe_version,
            policy_version=plan.policy_version,
            prompt_bytes=prompt_bytes,
            context_budget=plan.context_budget,
            estimated_prompt_tokens=plan.estimated_prompt_tokens,
            grounding_status=grounding_status,
            evidence=plan.evidence,
        )

    async def record_context_events(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        memory_candidate_count: int,
        evidence: tuple[EvidenceSnapshot, ...],
        web_search_status: WebSearchStatus,
        web_result_count: int,
    ) -> None:
        memory_keys = [item.key for item in evidence if item.kind == "memory"]
        await self._autobiography.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="retrieval_completed",
            actor_type="cortex",
            payload={
                "candidate_count": memory_candidate_count,
                "included_count": len(memory_keys),
                "evidence_keys": memory_keys,
            },
        )
        if web_search_status != "not_requested":
            await self.record_web_event(
                owner_id=owner_id,
                session_id=session_id,
                trace_id=trace_id,
                status=web_search_status,
                result_count=web_result_count,
                evidence_keys=[item.key for item in evidence if item.kind == "web"],
            )

    async def record_web_event(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        status: WebSearchStatus,
        result_count: int,
        evidence_keys: list[str],
    ) -> None:
        await self._autobiography.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="web_search_completed",
            actor_type="cortex",
            payload={
                "status": status,
                "result_count": result_count,
                "evidence_keys": evidence_keys,
            },
        )

    async def complete_generation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        generation_run_id: UUID,
        composed: ComposedResponse,
    ) -> StoredTurn:
        return await self._autobiography.complete_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            generation_run_id=generation_run_id,
            content=composed.answer,
            prompt_tokens=composed.prompt_tokens,
            completion_tokens=composed.completion_tokens,
            latency_ms=composed.latency_ms,
            finish_reason=composed.finish_reason,
            grounding_status=composed.grounding_status,
            citation_keys=tuple(citation.key for citation in composed.citations),
        )

    async def complete_policy_generation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        generation_run_id: UUID,
        answer: str,
    ) -> StoredTurn:
        return await self._autobiography.complete_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            generation_run_id=generation_run_id,
            content=answer,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=0.0,
            finish_reason="policy",
            grounding_status="abstained",
            citation_keys=(),
        )

    async def persist_failure(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        generation_run_id: UUID,
        error_code: str,
        retryable: bool,
        cancelled: bool = False,
    ) -> None:
        try:
            await self._session.rollback()
            await self._autobiography.fail_generation(
                owner_id=owner_id,
                session_id=session_id,
                trace_id=trace_id,
                generation_run_id=generation_run_id,
                error_code=error_code,
                retryable=retryable,
                cancelled=cancelled,
            )
            await self._session.commit()
        except Exception as persistence_error:
            await self._session.rollback()
            logger.exception(
                "typed_chat_failure_persistence_failed",
                extra={
                    "generation_run_id": str(generation_run_id),
                    "error_type": type(persistence_error).__name__,
                },
            )


__all__ = ["HistoryBundle", "TypedChatJournal"]
