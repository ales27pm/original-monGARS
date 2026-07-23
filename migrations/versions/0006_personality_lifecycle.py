"""Add approval-gated personality reset and deletion receipts.

Revision ID: 0006_personality_lifecycle
Revises: 0005_personality_profiles
Create Date: 2026-07-23

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_personality_lifecycle"
down_revision: str | Sequence[str] | None = "0005_personality_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "personality_profile_lifecycle",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("expected_profile_digest", sa.String(length=64), nullable=False),
        sa.Column("target_revision", sa.Integer(), nullable=False),
        sa.Column("target_profile_digest", sa.String(length=64), nullable=False),
        sa.Column("data_state_digest", sa.String(length=64), nullable=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation IN ('reset', 'delete')",
            name="ck_personality_lifecycle_operation",
        ),
        sa.CheckConstraint(
            "expected_revision >= 0 AND target_revision >= 0",
            name="ck_personality_lifecycle_revisions",
        ),
        sa.CheckConstraint(
            "expected_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_lifecycle_expected_digest",
        ),
        sa.CheckConstraint(
            "target_profile_digest ~ '^[0-9a-f]{64}$'",
            name="ck_personality_lifecycle_target_digest",
        ),
        sa.CheckConstraint(
            "(operation = 'reset' AND data_state_digest IS NULL) OR "
            "(operation = 'delete' AND data_state_digest ~ '^[0-9a-f]{64}$')",
            name="ck_personality_lifecycle_data_digest",
        ),
        sa.CheckConstraint(
            "(operation = 'reset' AND target_revision = expected_revision + 1) OR "
            "(operation = 'delete' AND target_revision = 0)",
            name="ck_personality_lifecycle_transition",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "task_id",
            name="uq_personality_lifecycle_owner_task",
        ),
    )
    op.create_index(
        "ix_personality_lifecycle_owner_created",
        "personality_profile_lifecycle",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    # Version 0005 cannot represent a positive-revision empty approved snapshot and has no
    # lifecycle receipt table. Refuse to discard either state rather than silently rewinding
    # revisions or erasing audit evidence.
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM personality_profile_lifecycle) "
            "OR EXISTS ("
            "SELECT 1 FROM personality_profiles "
            "WHERE source = 'approved_profile' AND preferences = '[]'::jsonb"
            ") THEN "
            "RAISE EXCEPTION 'cannot downgrade: personality lifecycle state exists'; "
            "END IF; END $$"
        )
    )
    op.drop_index(
        "ix_personality_lifecycle_owner_created",
        table_name="personality_profile_lifecycle",
    )
    op.drop_table("personality_profile_lifecycle")
