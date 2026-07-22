"""Create the initial monGARS control-plane schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "episodic_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_episodic_events_owner_created",
        "episodic_events",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_episodic_events_session_id",
        "episodic_events",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_episodic_events_trace",
        "episodic_events",
        ["trace_id"],
        unique=False,
    )

    op.create_table(
        "memory_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("source_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column(
            "sensitivity",
            sa.String(length=20),
            server_default=sa.text("'private'"),
            nullable=False,
        ),
        sa.Column(
            "retention_class",
            sa.String(length=20),
            server_default=sa.text("'keep'"),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_memory_document_retention",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_memory_document_sensitivity",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "source_sha256",
            name="uq_memory_document_owner_sha",
        ),
    )
    op.create_index(
        "ix_memory_documents_owner_created",
        "memory_documents",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )

    op.create_table(
        "memory_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("section_path", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("plaintext", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(dim=768), nullable=False),
        sa.Column("embedding_model", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["memory_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_memory_chunk_position",
        ),
    )
    op.create_index(
        "ix_memory_chunks_document",
        "memory_chunks",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_memory_chunks_embedding_hnsw",
        "memory_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "task_queue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            server_default=sa.text("'100'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("'0'"),
            nullable=False,
        ),
        sa.Column(
            "max_attempts",
            sa.Integer(),
            server_default=sa.text("'3'"),
            nullable=False,
        ),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("action_digest", sa.String(length=64), nullable=True),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
            "attempt_count >= 0",
            name="ck_task_queue_attempt_nonnegative",
        ),
        sa.CheckConstraint(
            "attempt_count <= max_attempts",
            name="ck_task_queue_attempt_within_limit",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR approved_at IS NOT NULL",
            name="ck_task_queue_consumption_requires_approval",
        ),
        sa.CheckConstraint(
            "max_attempts > 0",
            name="ck_task_queue_max_attempts_positive",
        ),
        sa.CheckConstraint(
            "risk_level = 'read_only' OR "
            "(action_digest IS NOT NULL AND approval_expires_at IS NOT NULL)",
            name="ck_task_queue_privileged_has_digest",
        ),
        sa.CheckConstraint(
            "risk_level = 'read_only' OR "
            "status IN ('waiting_approval', 'cancelled', 'failed') OR approved_at IS NOT NULL",
            name="ck_task_queue_privileged_execution_approved",
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 1000",
            name="ck_task_queue_priority_range",
        ),
        sa.CheckConstraint(
            "risk_level IN ('read_only', 'local_mutation', 'external_side_effect')",
            name="ck_task_queue_risk_level",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (lease_expires_at IS NOT NULL)",
            name="ck_task_queue_running_has_lease",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'done', 'failed', 'cancelled')",
            name="ck_task_queue_status",
        ),
        sa.ForeignKeyConstraint(
            ["parent_task_id"],
            ["task_queue.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_queue_claim",
        "task_queue",
        ["status", "run_after", sa.text("priority DESC"), "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_task_queue_owner_created",
        "task_queue",
        ["owner_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_task_queue_dedupe",
        "task_queue",
        ["owner_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )

    op.create_table(
        "inference_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inference_metrics_created",
        "inference_metrics",
        [sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_inference_metrics_created", table_name="inference_metrics")
    op.drop_table("inference_metrics")

    op.drop_index(
        "uq_task_queue_dedupe",
        table_name="task_queue",
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.drop_index("ix_task_queue_owner_created", table_name="task_queue")
    op.drop_index("ix_task_queue_claim", table_name="task_queue")
    op.drop_table("task_queue")

    op.drop_index(
        "ix_memory_chunks_embedding_hnsw",
        table_name="memory_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index("ix_memory_chunks_document", table_name="memory_chunks")
    op.drop_table("memory_chunks")

    op.drop_index("ix_memory_documents_owner_created", table_name="memory_documents")
    op.drop_table("memory_documents")

    op.drop_index("ix_episodic_events_trace", table_name="episodic_events")
    op.drop_index("ix_episodic_events_session_id", table_name="episodic_events")
    op.drop_index("ix_episodic_events_owner_created", table_name="episodic_events")
    op.drop_table("episodic_events")

    # Extensions are database-scoped and may be shared by other schemas, so a downgrade leaves
    # them installed deliberately.
