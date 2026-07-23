"""SQLAlchemy persistence models for explicit feedback and personality revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mongars.db.models import Base
from mongars.ids import uuid7


class ExplicitFeedbackRecord(Base):
    """One immutable owner-scoped explicit feedback submission."""

    __tablename__ = "explicit_feedback"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('correction', 'helpfulness', 'preference')",
            name="ck_explicit_feedback_kind",
        ),
        CheckConstraint(
            "feedback_digest ~ '^[0-9a-f]{64}$'",
            name="ck_explicit_feedback_digest",
        ),
        CheckConstraint(
            "response_trace_id IS NULL OR response_trace_id ~ '^trc_[0-9a-f]{32}$'",
            name="ck_explicit_feedback_trace",
        ),
        CheckConstraint(
            "applied_revision IS NULL OR applied_revision > 0",
            name="ck_explicit_feedback_applied_revision",
        ),
        CheckConstraint(
            "((applied_revision IS NULL) = (applied_task_id IS NULL))",
            name="ck_explicit_feedback_applied_pair",
        ),
        Index(
            "ix_explicit_feedback_owner_created",
            "owner_id",
            text("created_at DESC"),
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    feedback_id: Mapped[UUID] = mapped_column(primary_key=True)
    feedback_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    response_trace_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applied_task_id: Mapped[UUID | None] = mapped_column()
    applied_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PersonalityProfileRecord(Base):
    """Current owner-scoped personality projection used to build Cortex snapshots."""

    __tablename__ = "personality_profiles"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_personality_profile_revision"),
        CheckConstraint(
            "source IN ('approved_profile', 'explicit_feedback')",
            name="ck_personality_profile_source",
        ),
        CheckConstraint(
            "profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_profile_digest",
        ),
        CheckConstraint(
            "jsonb_typeof(preferences) = 'array'",
            name="ck_personality_profile_preferences_array",
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    preferences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PersonalityProfileRevisionRecord(Base):
    """Immutable audit row for one successfully applied personality revision."""

    __tablename__ = "personality_profile_revisions"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_personality_revision_positive"),
        CheckConstraint(
            "source IN ('approved_profile', 'explicit_feedback')",
            name="ck_personality_revision_source",
        ),
        CheckConstraint(
            "profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_profile_digest",
        ),
        CheckConstraint(
            "feedback_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_feedback_digest",
        ),
        CheckConstraint(
            "proposal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_proposal_digest",
        ),
        CheckConstraint(
            "changed_dimension IN "
            "('brevity', 'directness', 'formality', 'humor', 'initiative', "
            "'technical_depth')",
            name="ck_personality_revision_dimension",
        ),
        CheckConstraint(
            "jsonb_typeof(preferences) = 'array'",
            name="ck_personality_revision_preferences_array",
        ),
        ForeignKeyConstraint(
            ("owner_id", "feedback_id"),
            ("explicit_feedback.owner_id", "explicit_feedback.feedback_id"),
            name="fk_personality_revision_feedback",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_id",
            "task_id",
            name="uq_personality_revision_owner_task",
        ),
        Index(
            "ix_personality_revisions_owner_created",
            "owner_id",
            text("created_at DESC"),
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    preferences: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    feedback_id: Mapped[UUID] = mapped_column(nullable=False)
    feedback_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    changed_dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    conflict: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PersonalityProfileLifecycleRecord(Base):
    """Privacy-safe immutable receipt for a reviewed reset or deletion."""

    __tablename__ = "personality_profile_lifecycle"
    __table_args__ = (
        CheckConstraint(
            "operation IN ('reset', 'delete')",
            name="ck_personality_lifecycle_operation",
        ),
        CheckConstraint(
            "expected_revision >= 0 AND target_revision >= 0",
            name="ck_personality_lifecycle_revisions",
        ),
        CheckConstraint(
            "expected_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_lifecycle_expected_digest",
        ),
        CheckConstraint(
            "target_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_lifecycle_target_digest",
        ),
        CheckConstraint(
            "(operation = 'reset' AND data_state_digest IS NULL) OR "
            "(operation = 'delete' AND data_state_digest ~ '^[0-9a-f]{64}$')",
            name="ck_personality_lifecycle_data_digest",
        ),
        CheckConstraint(
            "(operation = 'reset' AND target_revision = expected_revision + 1) OR "
            "(operation = 'delete' AND target_revision = 0)",
            name="ck_personality_lifecycle_transition",
        ),
        UniqueConstraint(
            "owner_id",
            "task_id",
            name="uq_personality_lifecycle_owner_task",
        ),
        Index(
            "ix_personality_lifecycle_owner_created",
            "owner_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    target_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    target_profile_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    data_state_digest: Mapped[str | None] = mapped_column(String(64))
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = [
    "ExplicitFeedbackRecord",
    "PersonalityProfileLifecycleRecord",
    "PersonalityProfileRecord",
    "PersonalityProfileRevisionRecord",
]
