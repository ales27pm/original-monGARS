"""Application service for typed autobiographical memory transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mongars.autobiography.contracts import (
    EvidenceSnapshot,
    GroundingStatus,
    RetentionClass,
    Sensitivity,
    StoredTurn,
    normalize_event_payload,
)
from mongars.autobiography.repository import AutobiographyRepository
from mongars.autobiography.tables import GenerationRun


class AutobiographyService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = AutobiographyRepository(session)

    async def accept_user_turn(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        content: str,
        sensitivity: Sensitivity = "private",
        retention_class: RetentionClass = "keep",
    ) -> StoredTurn:
        turn = await self._repository.append_turn(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            role="user",
            content=content,
            state="accepted",
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=_expiry(retention_class),
        )
        await self.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="user_turn_accepted",
            actor_type="user",
            payload={
                "turn_id": turn.id,
                "role": "user",
                "ordinal": turn.ordinal,
                "character_count": len(turn.content),
            },
            sensitivity=sensitivity,
            retention_class=retention_class,
        )
        return turn

    async def begin_generation(
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
        sensitivity: Sensitivity = "private",
        retention_class: RetentionClass = "keep",
    ) -> GenerationRun:
        run = await self._repository.start_generation(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            user_turn_id=user_turn_id,
            model_alias=model_alias,
            model_digest=model_digest,
            prompt_recipe_version=prompt_recipe_version,
            policy_version=policy_version,
            prompt_bytes=prompt_bytes,
            context_budget=context_budget,
            estimated_prompt_tokens=estimated_prompt_tokens,
            grounding_status=grounding_status,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=_expiry(retention_class),
            evidence=evidence,
        )
        await self.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="generation_started",
            actor_type="bouche",
            payload={
                "generation_run_id": run.id,
                "user_turn_id": user_turn_id,
                "model_alias": model_alias,
                "model_digest": model_digest,
                "prompt_recipe_version": prompt_recipe_version,
                "policy_version": policy_version,
                "evidence_count": len(evidence),
            },
            sensitivity=sensitivity,
            retention_class=retention_class,
        )
        return run

    async def complete_generation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        trace_id: str,
        generation_run_id: UUID,
        content: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        latency_ms: float,
        finish_reason: str | None,
        grounding_status: GroundingStatus,
        citation_keys: tuple[str, ...],
        sensitivity: Sensitivity = "private",
        retention_class: RetentionClass = "keep",
    ) -> StoredTurn:
        assistant = await self._repository.append_turn(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            role="assistant",
            content=content,
            state="final",
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=_expiry(retention_class),
        )
        await self._repository.complete_generation(
            owner_id=owner_id,
            generation_run_id=generation_run_id,
            assistant_turn_id=assistant.id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            grounding_status=grounding_status,
        )
        await self.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="generation_completed",
            actor_type="bouche",
            payload={
                "generation_run_id": generation_run_id,
                "assistant_turn_id": assistant.id,
                "grounding_status": grounding_status,
                "citation_keys": list(citation_keys),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": latency_ms,
            },
            sensitivity=sensitivity,
            retention_class=retention_class,
        )
        await self.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="assistant_turn_committed",
            actor_type="bouche",
            payload={
                "turn_id": assistant.id,
                "generation_run_id": generation_run_id,
                "ordinal": assistant.ordinal,
            },
            sensitivity=sensitivity,
            retention_class=retention_class,
        )
        return assistant

    async def fail_generation(
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
        normalized_error = _event_error_code(error_code)
        await self._repository.fail_generation(
            owner_id=owner_id,
            generation_run_id=generation_run_id,
            error_code=normalized_error,
            cancelled=cancelled,
        )
        await self.record_event(
            owner_id=owner_id,
            session_id=session_id,
            trace_id=trace_id,
            event_type="generation_cancelled" if cancelled else "generation_failed",
            actor_type="bouche",
            payload=(
                {"generation_run_id": generation_run_id, "reason": normalized_error}
                if cancelled
                else {
                    "generation_run_id": generation_run_id,
                    "error_code": normalized_error,
                    "retryable": retryable,
                }
            ),
        )

    async def recent_conversation(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        limit: int,
    ) -> tuple[StoredTurn, ...]:
        return await self._repository.recent_conversation(
            owner_id=owner_id,
            session_id=session_id,
            limit=limit,
        )

    async def record_event(
        self,
        *,
        owner_id: str,
        trace_id: str,
        event_type: str,
        actor_type: str,
        payload: dict[str, object],
        session_id: UUID | None = None,
        sensitivity: Sensitivity = "private",
        retention_class: RetentionClass = "keep",
        source_occurred_at: datetime | None = None,
        causation_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> None:
        canonical = normalize_event_payload(event_type, payload)
        await self._repository.record_event(
            owner_id=owner_id,
            trace_id=trace_id,
            event_type=event_type,
            actor_type=actor_type,
            payload=canonical,
            session_id=session_id,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=_expiry(retention_class),
            source_occurred_at=source_occurred_at,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )


def _event_error_code(value: str) -> str:
    normalized = value.strip().casefold().replace("-", "_")
    if not normalized or len(normalized) > 100 or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in normalized
    ):
        return "generation_error"
    return normalized


def _expiry(retention_class: RetentionClass) -> datetime | None:
    now = datetime.now(UTC)
    if retention_class in {"keep", "legal_hold"}:
        return None
    if retention_class == "ttl_30d":
        return now + timedelta(days=30)
    return now + timedelta(days=90)


__all__ = ["AutobiographyService"]
