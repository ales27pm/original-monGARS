"""SQLAlchemy tables for typed turns, generation runs, evidence, and events."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mongars.db.models import Base
from mongars.ids import uuid7


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "session_id",
            "ordinal",
            name="uq_conversation_turn_owner_session_ordinal",
        ),
        UniqueConstraint(
            "id",
            "owner_id",
            "session_id",
            name="uq_conversation_turn_id_owner_session",
        ),
        CheckConstraint("ordinal > 0", name="ck_conversation_turn_ordinal_positive"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'policy')",
            name="ck_conversation_turn_role",
        ),
        CheckConstraint(
            "state IN ('accepted', 'generating', 'final', 'failed', 'cancelled', 'redacted')",
            name="ck_conversation_turn_state",
        ),
        CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_conversation_turn_sensitivity",
        ),
        CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_conversation_turn_retention",
        ),
        CheckConstraint(
            "octet_length(content_sha256) = 32",
            name="ck_conversation_turn_digest_length",
        ),
        Index(
            "ix_conversation_turns_owner_session_ordinal",
            "owner_id",
            "session_id",
            text("ordinal DESC"),
        ),
        Index("ix_conversation_turns_trace", "trace_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(20), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("user_turn_id", "owner_id", "session_id"),
            ("conversation_turns.id", "conversation_turns.owner_id", "conversation_turns.session_id"),
            name="fk_generation_run_user_turn_owner_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("assistant_turn_id", "owner_id", "session_id"),
            ("conversation_turns.id", "conversation_turns.owner_id", "conversation_turns.session_id"),
            name="fk_generation_run_assistant_turn_owner_session",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'failed', 'cancelled')",
            name="ck_generation_run_status",
        ),
        CheckConstraint(
            "grounding_status IN ('not_required', 'grounded', 'partially_grounded', 'abstained')",
            name="ck_generation_run_grounding",
        ),
        CheckConstraint(
            "model_digest IS NULL OR model_digest ~ '^[0-9a-f]{64}$'",
            name="ck_generation_run_model_digest",
        ),
        CheckConstraint(
            "octet_length(prompt_sha256) = 32",
            name="ck_generation_run_prompt_digest_length",
        ),
        CheckConstraint("context_budget > 0", name="ck_generation_run_context_budget"),
        CheckConstraint(
            "estimated_prompt_tokens >= 0",
            name="ck_generation_run_estimated_tokens",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_generation_run_latency",
        ),
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_generation_run_prompt_tokens",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_generation_run_completion_tokens",
        ),
        CheckConstraint(
            "((status = 'started') = (completed_at IS NULL))",
            name="ck_generation_run_completion_timestamp",
        ),
        CheckConstraint(
            "status != 'completed' OR assistant_turn_id IS NOT NULL",
            name="ck_generation_run_completed_has_assistant",
        ),
        Index("ix_generation_runs_owner_session_created", "owner_id", "session_id", "created_at"),
        Index("ix_generation_runs_trace", "trace_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[UUID] = mapped_column(nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    user_turn_id: Mapped[UUID] = mapped_column(nullable=False)
    assistant_turn_id: Mapped[UUID | None] = mapped_column()
    model_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    model_digest: Mapped[str | None] = mapped_column(String(64))
    prompt_recipe_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    context_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    grounding_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GenerationEvidence(Base):
    __tablename__ = "generation_evidence"
    __table_args__ = (
        UniqueConstraint(
            "generation_run_id",
            "evidence_key",
            name="uq_generation_evidence_run_key",
        ),
        CheckConstraint(
            "kind IN ('memory', 'web', 'conversation', 'policy')",
            name="ck_generation_evidence_kind",
        ),
        CheckConstraint(
            "evidence_key ~ '^[HMW][1-9][0-9]{0,2}$'",
            name="ck_generation_evidence_key",
        ),
        CheckConstraint("rank >= 0", name="ck_generation_evidence_rank"),
        CheckConstraint(
            "octet_length(retrieved_text_sha256) = 32",
            name="ck_generation_evidence_digest_length",
        ),
        Index("ix_generation_evidence_run_rank", "generation_run_id", "rank"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    generation_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False
    )
    evidence_key: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    source_uri: Mapped[str | None] = mapped_column(Text)
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    retrieved_text: Mapped[str] = mapped_column(Text, nullable=False)
    retrieved_text_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    included: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AutobiographicalEventRecord(Base):
    __tablename__ = "autobiographical_events"
    __table_args__ = (
        CheckConstraint("schema_version > 0", name="ck_autobiographical_event_schema_version"),
        CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_autobiographical_event_sensitivity",
        ),
        CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_autobiographical_event_retention",
        ),
        CheckConstraint(
            "octet_length(payload_sha256) = 32",
            name="ck_autobiographical_event_digest_length",
        ),
        Index("ix_autobiographical_events_owner_occurred", "owner_id", text("occurred_at DESC")),
        Index("ix_autobiographical_events_session_occurred", "session_id", text("occurred_at DESC")),
        Index("ix_autobiographical_events_trace", "trace_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[UUID | None] = mapped_column()
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column()
    correlation_id: Mapped[UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False)
    retention_class: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    payload_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


__all__ = [
    "AutobiographicalEventRecord",
    "ConversationTurn",
    "GenerationEvidence",
    "GenerationRun",
]
