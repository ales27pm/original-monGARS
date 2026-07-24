"""Add typed autobiographical turns, generation runs, evidence, and events.

Revision ID: 0007_autobiographical_memory
Revises: 0006_model_governance
Create Date: 2026-07-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_autobiographical_memory"
down_revision: str | Sequence[str] | None = "0006_model_governance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("sensitivity", sa.String(length=20), nullable=False),
        sa.Column("retention_class", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_conversation_turn_ordinal_positive",
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'policy')",
            name="ck_conversation_turn_role",
        ),
        sa.CheckConstraint(
            "state IN ('accepted', 'generating', 'final', 'failed', 'cancelled', 'redacted')",
            name="ck_conversation_turn_state",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_conversation_turn_sensitivity",
        ),
        sa.CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_conversation_turn_retention",
        ),
        sa.CheckConstraint(
            "octet_length(content_sha256) = 32",
            name="ck_conversation_turn_digest_length",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "session_id",
            "ordinal",
            name="uq_conversation_turn_owner_session_ordinal",
        ),
        sa.UniqueConstraint(
            "id",
            "owner_id",
            "session_id",
            name="uq_conversation_turn_id_owner_session",
        ),
    )
    op.create_index(
        "ix_conversation_turns_owner_session_ordinal",
        "conversation_turns",
        ["owner_id", "session_id", sa.text("ordinal DESC")],
    )
    op.create_index(
        "ix_conversation_turns_trace",
        "conversation_turns",
        ["trace_id"],
    )

    op.create_table(
        "generation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("user_turn_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_turn_id", sa.Uuid(), nullable=True),
        sa.Column("model_alias", sa.String(length=255), nullable=False),
        sa.Column("model_digest", sa.String(length=64), nullable=True),
        sa.Column("prompt_recipe_version", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("prompt_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("context_budget", sa.Integer(), nullable=False),
        sa.Column("estimated_prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("grounding_status", sa.String(length=32), nullable=False),
        sa.Column("sensitivity", sa.String(length=20), nullable=False),
        sa.Column("retention_class", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed', 'cancelled')",
            name="ck_generation_run_status",
        ),
        sa.CheckConstraint(
            "grounding_status IN "
            "('not_required', 'grounded', 'partially_grounded', 'abstained')",
            name="ck_generation_run_grounding",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_generation_run_sensitivity",
        ),
        sa.CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_generation_run_retention",
        ),
        sa.CheckConstraint(
            "model_digest IS NULL OR model_digest ~ '^[0-9a-f]{64}$'",
            name="ck_generation_run_model_digest",
        ),
        sa.CheckConstraint(
            "octet_length(prompt_sha256) = 32",
            name="ck_generation_run_prompt_digest_length",
        ),
        sa.CheckConstraint(
            "context_budget > 0",
            name="ck_generation_run_context_budget",
        ),
        sa.CheckConstraint(
            "estimated_prompt_tokens >= 0",
            name="ck_generation_run_estimated_tokens",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_generation_run_latency",
        ),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="ck_generation_run_prompt_tokens",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="ck_generation_run_completion_tokens",
        ),
        sa.CheckConstraint(
            "((status = 'started') = (completed_at IS NULL))",
            name="ck_generation_run_completion_timestamp",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR assistant_turn_id IS NOT NULL",
            name="ck_generation_run_completed_has_assistant",
        ),
        sa.ForeignKeyConstraint(
            ["user_turn_id", "owner_id", "session_id"],
            [
                "conversation_turns.id",
                "conversation_turns.owner_id",
                "conversation_turns.session_id",
            ],
            name="fk_generation_run_user_turn_owner_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assistant_turn_id", "owner_id", "session_id"],
            [
                "conversation_turns.id",
                "conversation_turns.owner_id",
                "conversation_turns.session_id",
            ],
            name="fk_generation_run_assistant_turn_owner_session",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_generation_runs_owner_session_created",
        "generation_runs",
        ["owner_id", "session_id", "created_at"],
    )
    op.create_index(
        "ix_generation_runs_trace",
        "generation_runs",
        ["trace_id"],
    )

    op.create_table(
        "generation_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("generation_run_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_key", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column(
            "locator",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("retrieved_text", sa.Text(), nullable=False),
        sa.Column(
            "retrieved_text_sha256",
            sa.LargeBinary(length=32),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "included",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('memory', 'web', 'conversation', 'policy')",
            name="ck_generation_evidence_kind",
        ),
        sa.CheckConstraint(
            "evidence_key ~ '^[HMWP][1-9][0-9]{0,2}$'",
            name="ck_generation_evidence_key",
        ),
        sa.CheckConstraint(
            "rank >= 0",
            name="ck_generation_evidence_rank",
        ),
        sa.CheckConstraint(
            "octet_length(retrieved_text_sha256) = 32",
            name="ck_generation_evidence_digest_length",
        ),
        sa.ForeignKeyConstraint(
            ["generation_run_id"],
            ["generation_runs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "generation_run_id",
            "evidence_key",
            name="uq_generation_evidence_run_key",
        ),
    )
    op.create_index(
        "ix_generation_evidence_run_rank",
        "generation_evidence",
        ["generation_run_id", "rank"],
    )

    op.create_table(
        "autobiographical_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "source_occurred_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("sensitivity", sa.String(length=20), nullable=False),
        sa.Column("retention_class", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_autobiographical_event_schema_version",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_autobiographical_event_sensitivity",
        ),
        sa.CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_autobiographical_event_retention",
        ),
        sa.CheckConstraint(
            "octet_length(payload_sha256) = 32",
            name="ck_autobiographical_event_digest_length",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_autobiographical_events_owner_occurred",
        "autobiographical_events",
        ["owner_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_autobiographical_events_session_occurred",
        "autobiographical_events",
        ["session_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_autobiographical_events_trace",
        "autobiographical_events",
        ["trace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_autobiographical_events_trace",
        table_name="autobiographical_events",
    )
    op.drop_index(
        "ix_autobiographical_events_session_occurred",
        table_name="autobiographical_events",
    )
    op.drop_index(
        "ix_autobiographical_events_owner_occurred",
        table_name="autobiographical_events",
    )
    op.drop_table("autobiographical_events")
    op.drop_index(
        "ix_generation_evidence_run_rank",
        table_name="generation_evidence",
    )
    op.drop_table("generation_evidence")
    op.drop_index("ix_generation_runs_trace", table_name="generation_runs")
    op.drop_index(
        "ix_generation_runs_owner_session_created",
        table_name="generation_runs",
    )
    op.drop_table("generation_runs")
    op.drop_index(
        "ix_conversation_turns_trace",
        table_name="conversation_turns",
    )
    op.drop_index(
        "ix_conversation_turns_owner_session_ordinal",
        table_name="conversation_turns",
    )
    op.drop_table("conversation_turns")
