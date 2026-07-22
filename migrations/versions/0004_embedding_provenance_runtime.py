"""Pin embedding spaces, preserve document locators, and publish runtime health.

Revision ID: 0004_embedding_provenance
Revises: 0003_document_staging
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# Alembic's default ``alembic_version.version_num`` is VARCHAR(32). Keep every
# revision identifier below that hard database boundary so a pristine upgrade
# does not fail while recording the revision.
revision: str = "0004_embedding_provenance"
down_revision: str | Sequence[str] | None = "0003_document_staging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The signed task contract now binds the trusted server receipt time. Existing
    # document-ingest approvals cannot be rewritten without the application HMAC key,
    # so fail them closed before the worker can claim them and remove their staged
    # private bytes. The durable task remains visible with an explicit re-upload
    # instruction instead of failing later as an apparently valid execution.
    op.execute(
        sa.text(
            "DELETE FROM document_staging AS staging USING task_queue AS task "
            "WHERE staging.task_id = task.id "
            "AND task.kind = 'document.ingest' "
            "AND task.status IN ('waiting_approval', 'queued', 'running')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE task_queue SET status = 'cancelled', "
            "error_text = 'upgrade requires document re-upload under receipt-bound approval', "
            "lease_expires_at = NULL, execution_token = NULL, consumed_at = NULL, "
            "updated_at = now() "
            "WHERE kind = 'document.ingest' "
            "AND status IN ('waiting_approval', 'queued', 'running')"
        )
    )

    op.add_column(
        "memory_chunks",
        sa.Column(
            "locator",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_table(
        "memory_chunk_embeddings",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_space_id", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_alias", sa.String(length=255), nullable=False),
        sa.Column("model_digest", sa.String(length=64), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("normalization_policy", sa.String(length=20), nullable=False),
        sa.Column("document_instruction", sa.Text(), nullable=False),
        sa.Column("query_instruction", sa.Text(), nullable=False),
        sa.Column("clustering_instruction", sa.Text(), nullable=False),
        sa.Column("classification_instruction", sa.Text(), nullable=False),
        sa.Column("truncate", sa.Boolean(), nullable=False),
        sa.Column("maximum_input_bytes", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.String(length=100), nullable=False),
        sa.Column("embedding", Vector(dim=768), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dimension = 768",
            name="ck_memory_chunk_embedding_dimension",
        ),
        sa.CheckConstraint(
            "normalization_policy IN ('none', 'l2')",
            name="ck_memory_chunk_embedding_normalization",
        ),
        sa.CheckConstraint(
            "truncate = false",
            name="ck_memory_chunk_embedding_no_truncation",
        ),
        sa.CheckConstraint(
            "maximum_input_bytes > 0",
            name="ck_memory_chunk_embedding_positive_input_limit",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["memory_chunks.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_space_id"),
    )

    # Vectors from releases before this migration have neither Nomic retrieval
    # instructions nor an immutable artifact digest. Preserve them in a deliberately
    # incompatible legacy space so an approved shadow reindex can replace them.
    op.execute(
        sa.text(
            "INSERT INTO memory_chunk_embeddings ("
            "chunk_id, embedding_space_id, provider, model_alias, model_digest, dimension, "
            "normalization_policy, document_instruction, query_instruction, "
            "clustering_instruction, classification_instruction, truncate, "
            "maximum_input_bytes, profile_version, embedding, created_at"
            ") SELECT id, "
            "encode(digest(convert_to('legacy-uninstructed:' || embedding_model, 'UTF8'), "
            "'sha256'), 'hex'), "
            "'ollama', embedding_model, repeat('0', 64), 768, 'none', '', '', '', '', "
            "false, 32000, 'legacy-uninstructed-v0', embedding, created_at "
            "FROM memory_chunks"
        )
    )
    op.create_index(
        "ix_memory_chunk_embeddings_space",
        "memory_chunk_embeddings",
        ["embedding_space_id", "chunk_id"],
        unique=False,
    )
    op.create_index(
        "ix_memory_chunk_embeddings_hnsw",
        "memory_chunk_embeddings",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # Expand-only rollout: retain the old columns and index until every application
    # process has switched to ``memory_chunk_embeddings``. Old workers can continue
    # writing their reviewed legacy representation during a rolling transition; new
    # workers leave these compatibility columns NULL and readiness requires reindexing
    # before retrieval. A later contract migration may remove them after observation.
    op.alter_column("memory_chunks", "embedding", nullable=True)
    op.alter_column("memory_chunks", "embedding_model", nullable=True)

    op.add_column(
        "document_staging",
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # Existing staging rows predate the dedicated receipt field. Their database
    # creation time is the closest trusted server observation; never substitute the
    # client-supplied source timestamp or the migration execution time.
    op.execute(sa.text("UPDATE document_staging SET received_at = created_at"))

    op.create_table(
        "runtime_components",
        sa.Column("component_id", sa.String(length=100), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("component_type", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=100), nullable=False),
        sa.Column("git_sha", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('healthy', 'degraded', 'unhealthy')",
            name="ck_runtime_component_status",
        ),
        sa.PrimaryKeyConstraint("component_id"),
    )
    op.create_index(
        "ix_runtime_components_type_seen",
        "runtime_components",
        ["component_type", sa.text("last_seen_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_components_type_seen", table_name="runtime_components")
    op.drop_table("runtime_components")
    op.drop_column("document_staging", "received_at")

    # The pre-0004 application embeds queries without retrieval instructions. It can
    # only consume the preserved legacy vector for the exact, unchanged chunk text.
    # Refuse a downgrade once a new/rechunked row has no such representation rather
    # than silently restoring an incompatible instructed vector.
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS ("
            "SELECT 1 FROM memory_chunks AS chunk "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM memory_chunk_embeddings AS embedding "
            "WHERE embedding.chunk_id = chunk.id "
            "AND embedding.profile_version = 'legacy-uninstructed-v0'"
            ")"
            ") THEN "
            "RAISE EXCEPTION 'cannot downgrade: one or more chunks have no legacy embedding'; "
            "END IF; END $$"
        )
    )
    op.execute(
        sa.text(
            "UPDATE memory_chunks AS chunk SET "
            "embedding = selected.embedding, embedding_model = selected.model_alias "
            "FROM ("
            "SELECT DISTINCT ON (chunk_id) chunk_id, embedding, model_alias "
            "FROM memory_chunk_embeddings "
            "WHERE profile_version = 'legacy-uninstructed-v0' "
            "ORDER BY chunk_id, created_at DESC, embedding_space_id DESC"
            ") AS selected WHERE selected.chunk_id = chunk.id"
        )
    )
    op.alter_column("memory_chunks", "embedding", nullable=False)
    op.alter_column("memory_chunks", "embedding_model", nullable=False)
    op.drop_index(
        "ix_memory_chunk_embeddings_hnsw",
        table_name="memory_chunk_embeddings",
        postgresql_using="hnsw",
    )
    op.drop_index(
        "ix_memory_chunk_embeddings_space",
        table_name="memory_chunk_embeddings",
    )
    op.drop_table("memory_chunk_embeddings")
    op.drop_column("memory_chunks", "locator")
