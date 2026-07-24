"""Owner-scoped repository for typed autobiographical memory."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.autobiography.contracts import (
    EvidenceSnapshot,
    GroundingStatus,
    RetentionClass,
    Sensitivity,
    StoredTurn,
    TurnRole,
    TurnState,
)
from mongars.autobiography.tables import (
    AutobiographicalEventRecord,
    ConversationTurn,
    GenerationEvidence,
    GenerationRun,
)
from mongars.ids import uuid7
from mongars.inference.base import JsonValue


class AutobiographyStateError(RuntimeError):
    """Raised when a generation transition no longer owns the expected state."""


class AutobiographyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_turn(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        role: TurnRole,
        content: str,
        state: TurnState,
        sensitivity: Sensitivity,
        retention_class: RetentionClass,
        expires_at: datetime | None,
    ) -> StoredTurn:
        normalized = _bounded_content(content)
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
            {"scope": f"conversation:{owner_id}:{session_id}"},
        )
        current = await self._session.scalar(
            select(func.max(ConversationTurn.ordinal)).where(
                ConversationTurn.owner_id == owner_id,
                ConversationTurn.session_id == session_id,
            )
        )
        ordinal = int(current or 0) + 1
        row = ConversationTurn(
            id=uuid7(),
            owner_id=owner_id,
            session_id=session_id,
            ordinal=ordinal,
            trace_id=trace_id,
            role=role,
            content=normalized,
            content_sha256=hashlib.sha256(normalized.encode("utf-8")).digest(),
            state=state,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _stored_turn(row)

    async def recent_conversation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        limit: int,
    ) -> tuple[StoredTurn, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("conversation history limit must be between 1 and 100")
        statement = (
            select(ConversationTurn)
            .where(
                ConversationTurn.owner_id == owner_id,
                ConversationTurn.session_id == session_id,
                ConversationTurn.state.in_(("accepted", "final")),
                ConversationTurn.role.in_(("user", "assistant")),
                (ConversationTurn.expires_at.is_(None) | (ConversationTurn.expires_at > func.now())),
            )
            .order_by(ConversationTurn.ordinal.desc())
            .limit(limit)
        )
        rows = list(reversed((await self._session.scalars(statement)).all()))
        return tuple(_stored_turn(row) for row in rows)

    async def start_generation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        user_turn_id: UUID,
        model_alias: str,
        model_digest: str | None,
        prompt_recipe_version: str,
        policy_version: str,
        prompt_bytes: bytes,
        context_budget: int,
        estimated_prompt_tokens: int,
        grounding_status: GroundingStatus,
        evidence: tuple[EvidenceSnapshot, ...],
    ) -> GenerationRun:
        run = GenerationRun(
            id=uuid7(),
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            user_turn_id=user_turn_id,
            assistant_turn_id=None,
            model_alias=model_alias,
            model_digest=model_digest,
            prompt_recipe_version=prompt_recipe_version,
            policy_version=policy_version,
            prompt_sha256=hashlib.sha256(prompt_bytes).digest(),
            context_budget=context_budget,
            estimated_prompt_tokens=estimated_prompt_tokens,
            prompt_tokens=None,
            completion_tokens=None,
            latency_ms=None,
            finish_reason=None,
            grounding_status=grounding_status,
            status="started",
            error_code=None,
        )
        self._session.add(run)
        await self._session.flush()
        self._session.add_all(
            GenerationEvidence(
                id=uuid7(),
                generation_run_id=run.id,
                evidence_key=item.key,
                kind=item.kind,
                source_id=item.source_id,
                title=item.title,
                source_uri=item.source_uri,
                locator=dict(item.locator or {}),
                retrieved_text=item.text,
                retrieved_text_sha256=hashlib.sha256(item.text.encode("utf-8")).digest(),
                score=item.score,
                rank=item.rank,
                retrieved_at=item.retrieved_at,
                included=item.included,
            )
            for item in evidence
        )
        await self._session.flush()
        return run

    async def complete_generation(
        self,
        *,
        owner_id: str,
        generation_run_id: UUID,
        assistant_turn_id: UUID,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: float,
        finish_reason: str | None,
        grounding_status: GroundingStatus,
    ) -> GenerationRun:
        run = await self._lock_generation(owner_id=owner_id, generation_run_id=generation_run_id)
        if run.status != "started":
            raise AutobiographyStateError("generation is no longer in the started state")
        run.assistant_turn_id = assistant_turn_id
        run.prompt_tokens = prompt_tokens
        run.completion_tokens = completion_tokens
        run.latency_ms = latency_ms
        run.finish_reason = finish_reason
        run.grounding_status = grounding_status
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        await self._session.flush()
        return run

    async def fail_generation(
        self,
        *,
        owner_id: str,
        generation_run_id: UUID,
        error_code: str,
        cancelled: bool,
    ) -> GenerationRun:
        run = await self._lock_generation(owner_id=owner_id, generation_run_id=generation_run_id)
        if run.status != "started":
            raise AutobiographyStateError("generation is no longer in the started state")
        run.status = "cancelled" if cancelled else "failed"
        run.error_code = _safe_error_code(error_code)
        run.completed_at = datetime.now(UTC)
        await self._session.flush()
        return run

    async def record_event(
        self,
        *,
        owner_id: str,
        trace_id: str,
        event_type: str,
        actor_type: str,
        payload: dict[str, JsonValue],
        session_id: UUID | None,
        sensitivity: Sensitivity,
        retention_class: RetentionClass,
        source_occurred_at: datetime | None = None,
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
        schema_version: int = 1,
    ) -> AutobiographicalEventRecord:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        row = AutobiographicalEventRecord(
            id=uuid7(),
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type=event_type,
            schema_version=schema_version,
            actor_type=actor_type,
            causation_id=causation_id,
            correlation_id=correlation_id,
            source_occurred_at=source_occurred_at,
            sensitivity=sensitivity,
            retention_class=retention_class,
            payload=payload,
            payload_sha256=hashlib.sha256(canonical).digest(),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def _lock_generation(
        self,
        *,
        owner_id: str,
        generation_run_id: UUID,
    ) -> GenerationRun:
        statement = (
            select(GenerationRun)
            .where(
                GenerationRun.id == generation_run_id,
                GenerationRun.owner_id == owner_id,
            )
            .with_for_update()
        )
        row = cast(GenerationRun | None, await self._session.scalar(statement))
        if row is None:
            raise AutobiographyStateError("generation run does not exist for this owner")
        return row


def _stored_turn(row: ConversationTurn) -> StoredTurn:
    return StoredTurn(
        id=row.id,
        owner_id=row.owner_id,
        session_id=row.session_id,
        ordinal=row.ordinal,
        trace_id=row.trace_id,
        role=cast(TurnRole, row.role),
        content=row.content,
        state=cast(TurnState, row.state),
        sensitivity=cast(Sensitivity, row.sensitivity),
        retention_class=cast(RetentionClass, row.retention_class),
        created_at=row.created_at,
    )


def _bounded_content(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("turn content must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("turn content must not be empty")
    if len(normalized) > 2_000_000:
        raise ValueError("turn content exceeds the configured hard ceiling")
    return normalized


def _safe_error_code(value: str) -> str:
    normalized = value.strip().casefold().replace("-", "_")
    if not normalized or len(normalized) > 100 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in normalized
    ):
        return "generation_error"
    return normalized


__all__ = ["AutobiographyRepository", "AutobiographyStateError"]
