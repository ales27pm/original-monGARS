"""Add bounded owner-scoped document upload staging.

Revision ID: 0003_document_staging
Revises: 0002_runtime_consistency
Create Date: 2026-07-22

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_document_staging"
down_revision: str | Sequence[str] | None = "0002_runtime_consistency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_task_queue_id_owner",
        "task_queue",
        ["id", "owner_id"],
    )
    op.create_table(
        "document_staging",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("detected_mime_type", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(content) = byte_size",
            name="ck_document_staging_content_size",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_document_staging_positive_size",
        ),
        sa.CheckConstraint(
            "byte_size <= 20000000",
            name="ck_document_staging_max_size",
        ),
        sa.CheckConstraint(
            "octet_length(source_sha256) = 32",
            name="ck_document_staging_sha256_length",
        ),
        sa.ForeignKeyConstraint(
            ["task_id", "owner_id"],
            ["task_queue.id", "task_queue.owner_id"],
            name="fk_document_staging_task_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        "ix_document_staging_owner_created",
        "document_staging",
        ["owner_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_document_staging_expires",
        "document_staging",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_staging_expires", table_name="document_staging")
    op.drop_index("ix_document_staging_owner_created", table_name="document_staging")
    op.drop_table("document_staging")
    op.drop_constraint("uq_task_queue_id_owner", "task_queue", type_="unique")
