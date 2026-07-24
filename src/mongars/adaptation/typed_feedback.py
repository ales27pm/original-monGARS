"""Bridge explicit Mimicry feedback to typed autobiographical chat records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.autobiography.contracts import RetentionClass, Sensitivity
from mongars.autobiography.service import AutobiographyService
from mongars.autobiography.tables import GenerationRun
from mongars.db.models import EpisodicEvent


class ResponseTraceNotFound(LookupError):
    """Raised when explicit feedback does not target an owned completed response."""


class ResponseTraceIntegrityError(RuntimeError):
    """Raised when one trace resolves to multiple completed typed generations."""


@dataclass(frozen=True, slots=True)
class ResolvedResponseTarget:
    """Owner-scoped response identity across typed and legacy chat storage."""

    trace_id: str
    generation_run_id: UUID | None
    session_id: UUID | None
    assistant_turn_id: UUID | None
    sensitivity: Sensitivity | None
    retention_class: RetentionClass | None

    @property
    def is_typed(self) -> bool:
        return (
            self.generation_run_id is not None
            and self.session_id is not None
            and self.assistant_turn_id is not None
            and self.sensitivity is not None
            and self.retention_class is not None
        )


async def resolve_owned_response_target(
    *,
    session: AsyncSession,
    owner_id: str,
    trace_id: str,
) -> ResolvedResponseTarget:
    """Resolve a completed typed response, then fall back to legacy message events."""

    typed_statement = (
        select(
            GenerationRun.id,
            GenerationRun.session_id,
            GenerationRun.assistant_turn_id,
            GenerationRun.sensitivity,
            GenerationRun.retention_class,
        )
        .where(
            GenerationRun.owner_id == owner_id,
            GenerationRun.trace_id == trace_id,
            GenerationRun.status == "completed",
            GenerationRun.assistant_turn_id.is_not(None),
        )
        .order_by(GenerationRun.created_at.desc())
        .limit(2)
    )
    typed_rows = (await session.execute(typed_statement)).all()
    if len(typed_rows) > 1:
        raise ResponseTraceIntegrityError(
            "response trace resolves to multiple completed typed generations"
        )
    if typed_rows:
        (
            generation_run_id,
            session_id,
            assistant_turn_id,
            sensitivity,
            retention_class,
        ) = typed_rows[0]
        return ResolvedResponseTarget(
            trace_id=trace_id,
            generation_run_id=cast(UUID, generation_run_id),
            session_id=cast(UUID, session_id),
            assistant_turn_id=cast(UUID, assistant_turn_id),
            sensitivity=cast(Sensitivity, sensitivity),
            retention_class=cast(RetentionClass, retention_class),
        )

    legacy_statement = (
        select(EpisodicEvent.id)
        .where(
            EpisodicEvent.owner_id == owner_id,
            EpisodicEvent.trace_id == trace_id,
            EpisodicEvent.actor == "cortex",
            EpisodicEvent.event_type == "message",
        )
        .limit(1)
    )
    if await session.scalar(legacy_statement) is not None:
        return ResolvedResponseTarget(
            trace_id=trace_id,
            generation_run_id=None,
            session_id=None,
            assistant_turn_id=None,
            sensitivity=None,
            retention_class=None,
        )
    raise ResponseTraceNotFound("response trace not found")


async def record_typed_feedback_event(
    *,
    session: AsyncSession,
    owner_id: str,
    target: ResolvedResponseTarget,
    feedback: ExplicitFeedback,
) -> None:
    """Append one content-minimized typed event for a newly accepted feedback record."""

    if not target.is_typed:
        return
    generation_run_id = cast(UUID, target.generation_run_id)
    session_id = cast(UUID, target.session_id)
    assistant_turn_id = cast(UUID, target.assistant_turn_id)
    sensitivity = cast(Sensitivity, target.sensitivity)
    retention_class = cast(RetentionClass, target.retention_class)
    autobiography = AutobiographyService(session)
    common = {
        "owner_id": owner_id,
        "session_id": session_id,
        "trace_id": target.trace_id,
        "actor_type": "user",
        "sensitivity": sensitivity,
        "retention_class": retention_class,
        "causation_id": generation_run_id,
        "correlation_id": feedback.feedback_id,
    }

    if isinstance(feedback, CorrectionFeedback):
        await autobiography.record_event(
            **common,
            event_type="correction_received",
            payload={
                "target_turn_id": assistant_turn_id,
                "correction_id": feedback.feedback_id,
                "character_count": len(feedback.correction_text),
            },
        )
        return

    if isinstance(feedback, HelpfulnessFeedback):
        await autobiography.record_event(
            **common,
            event_type="feedback_received",
            payload={
                "target_turn_id": assistant_turn_id,
                "rating": "up" if feedback.helpful else "down",
                "tags": ["explicit_helpfulness"],
            },
        )
        return

    if isinstance(feedback, PreferenceFeedback):
        await autobiography.record_event(
            **common,
            event_type="feedback_received",
            payload={
                "target_turn_id": assistant_turn_id,
                "rating": "neutral",
                "tags": [
                    "explicit_preference",
                    f"dimension:{feedback.dimension}",
                ],
            },
        )
        return

    raise TypeError("unsupported explicit feedback value")


__all__ = [
    "ResolvedResponseTarget",
    "ResponseTraceIntegrityError",
    "ResponseTraceNotFound",
    "record_typed_feedback_event",
    "resolve_owned_response_target",
]
