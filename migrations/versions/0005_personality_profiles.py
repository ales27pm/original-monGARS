"""Persist explicit feedback and owner-reviewed personality profile revisions.

Revision ID: 0005_personality_profiles
Revises: 0004_embedding_provenance
Create Date: 2026-07-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_personality_profiles"
down_revision: str | Sequence[str] | None = "0004_embedding_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "explicit_feedback",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_digest", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("response_trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("applied_task_id", sa.Uuid(), nullable=True),
        sa.Column("applied_revision", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('correction', 'helpfulness', 'preference')",
            name="ck_explicit_feedback_kind",
        ),
        sa.CheckConstraint(
            "feedback_digest ~ '^[0-9a-f]{64}$'",
            name="ck_explicit_feedback_digest",
        ),
        sa.CheckConstraint(
            "response_trace_id IS NULL OR response_trace_id ~ '^trc_[0-9a-f]{32}$'",
            name="ck_explicit_feedback_trace",
        ),
        sa.CheckConstraint(
            "applied_revision IS NULL OR applied_revision > 0",
            name="ck_explicit_feedback_applied_revision",
        ),
        sa.CheckConstraint(
            "((applied_revision IS NULL) = (applied_task_id IS NULL))",
            name="ck_explicit_feedback_applied_pair",
        ),
        sa.PrimaryKeyConstraint("owner_id", "feedback_id"),
    )
    op.create_index(
        "ix_explicit_feedback_owner_created",
        "explicit_feedback",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "personality_profiles",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_personality_profile_revision",
        ),
        sa.CheckConstraint(
            "source IN ('approved_profile', 'explicit_feedback')",
            name="ck_personality_profile_source",
        ),
        sa.CheckConstraint(
            "profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_profile_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preferences) = 'array'",
            name="ck_personality_profile_preferences_array",
        ),
        sa.PrimaryKeyConstraint("owner_id"),
    )

    op.create_table(
        "personality_profile_revisions",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("profile_digest", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "preferences",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("feedback_digest", sa.String(length=64), nullable=False),
        sa.Column("proposal_digest", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("changed_dimension", sa.String(length=32), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_personality_revision_positive",
        ),
        sa.CheckConstraint(
            "source IN ('approved_profile', 'explicit_feedback')",
            name="ck_personality_revision_source",
        ),
        sa.CheckConstraint(
            "profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_profile_digest",
        ),
        sa.CheckConstraint(
            "feedback_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_feedback_digest",
        ),
        sa.CheckConstraint(
            "proposal_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_revision_proposal_digest",
        ),
        sa.CheckConstraint(
            "changed_dimension IN "
            "('brevity', 'directness', 'formality', 'humor', 'initiative', "
            "'technical_depth')",
            name="ck_personality_revision_dimension",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preferences) = 'array'",
            name="ck_personality_revision_preferences_array",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "feedback_id"],
            ["explicit_feedback.owner_id", "explicit_feedback.feedback_id"],
            name="fk_personality_revision_feedback",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "revision"),
        sa.UniqueConstraint(
            "owner_id",
            "task_id",
            name="uq_personality_revision_owner_task",
        ),
    )
    op.create_index(
        "ix_personality_revisions_owner_created",
        "personality_profile_revisions",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_personality_revisions_owner_created",
        table_name="personality_profile_revisions",
    )
    op.drop_table("personality_profile_revisions")
    op.drop_table("personality_profiles")
    op.drop_index(
        "ix_explicit_feedback_owner_created",
        table_name="explicit_feedback",
    )
    op.drop_table("explicit_feedback")
