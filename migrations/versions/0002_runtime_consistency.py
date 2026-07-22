"""Harden task execution and memory retrieval consistency.

Revision ID: 0002_runtime_consistency
Revises: 0001_initial
Create Date: 2026-07-22

"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_runtime_consistency"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _provenance_digest(
    *,
    source_type: str,
    source_uri: str | None,
    title: str | None,
    mime_type: str | None,
    metadata: dict[str, object],
) -> bytes:
    canonical = json.dumps(
        {
            "source_type": source_type,
            "source_uri": source_uri,
            "title": title,
            "mime_type": mime_type,
            "metadata": metadata,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).digest()


def upgrade() -> None:
    op.add_column(
        "memory_chunks",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, plaintext)", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_memory_chunks_search_vector_gin",
        "memory_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )

    op.create_table(
        "memory_document_provenance",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("provenance_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata",
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
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["memory_documents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "provenance_sha256",
            name="uq_memory_document_provenance_digest",
        ),
    )
    op.create_index(
        "ix_memory_document_provenance_document",
        "memory_document_provenance",
        ["document_id", "created_at"],
        unique=False,
    )

    # Preserve the canonical source observation for documents created before this
    # migration. The digest deliberately matches the application implementation so a
    # later retry of the same observation remains idempotent.
    connection = op.get_bind()
    existing_documents = connection.execute(
        sa.text(
            "SELECT id, source_type, source_uri, title, mime_type, metadata, created_at "
            "FROM memory_documents"
        )
    ).mappings()
    provenance_table = sa.table(
        "memory_document_provenance",
        sa.column("id", sa.Uuid()),
        sa.column("document_id", sa.Uuid()),
        sa.column("provenance_sha256", sa.LargeBinary(length=32)),
        sa.column("source_type", sa.String(length=50)),
        sa.column("source_uri", sa.Text()),
        sa.column("title", sa.Text()),
        sa.column("mime_type", sa.String(length=255)),
        sa.column("metadata", postgresql.JSONB()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    provenance_rows: list[dict[str, object]] = []
    for document in existing_documents:
        metadata = dict(document["metadata"] or {})
        source_type = str(document["source_type"])
        source_uri = document["source_uri"]
        title = document["title"]
        mime_type = document["mime_type"]
        provenance_rows.append(
            {
                "id": uuid4(),
                "document_id": document["id"],
                "provenance_sha256": _provenance_digest(
                    source_type=source_type,
                    source_uri=source_uri,
                    title=title,
                    mime_type=mime_type,
                    metadata=metadata,
                ),
                "source_type": source_type,
                "source_uri": source_uri,
                "title": title,
                "mime_type": mime_type,
                "metadata": metadata,
                "created_at": document["created_at"],
            }
        )
    if provenance_rows:
        connection.execute(provenance_table.insert(), provenance_rows)

    op.add_column("task_queue", sa.Column("execution_token", sa.Uuid(), nullable=True))
    op.drop_constraint(
        "ck_task_queue_running_has_lease",
        "task_queue",
        type_="check",
    )
    # Existing running rows are not safe to resume after the deploy. Retryable rows
    # are requeued for a tokenized claim; rows already on their final attempt fail.
    op.execute(
        sa.text(
            "UPDATE task_queue "
            "SET status = CASE "
            "        WHEN attempt_count >= max_attempts THEN 'failed' "
            "        ELSE 'queued' "
            "    END, "
            "lease_expires_at = NULL, execution_token = NULL, "
            "error_text = CASE "
            "        WHEN attempt_count >= max_attempts "
            "            THEN 'worker upgraded after final attempt; task failed' "
            "        ELSE 'worker upgraded; task requeued' "
            "    END "
            "WHERE status = 'running'"
        )
    )
    op.create_check_constraint(
        "ck_task_queue_running_has_lease",
        "task_queue",
        "((status = 'running') = (lease_expires_at IS NOT NULL)) AND "
        "((status = 'running') = (execution_token IS NOT NULL))",
    )
    # Repair rows stranded by the old recovery behavior before this migration:
    # claim_next() cannot select a queued task that has already exhausted its attempts.
    op.execute(
        sa.text(
            "UPDATE task_queue "
            "SET status = 'failed', "
            "    error_text = 'task exhausted all attempts before worker upgrade; task failed' "
            "WHERE status = 'queued' AND attempt_count >= max_attempts"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_task_queue_running_has_lease",
        "task_queue",
        type_="check",
    )
    op.create_check_constraint(
        "ck_task_queue_running_has_lease",
        "task_queue",
        "(status = 'running') = (lease_expires_at IS NOT NULL)",
    )
    op.drop_column("task_queue", "execution_token")

    op.drop_index(
        "ix_memory_document_provenance_document",
        table_name="memory_document_provenance",
    )
    op.drop_table("memory_document_provenance")

    op.drop_index(
        "ix_memory_chunks_search_vector_gin",
        table_name="memory_chunks",
        postgresql_using="gin",
    )
    op.drop_column("memory_chunks", "search_vector")
