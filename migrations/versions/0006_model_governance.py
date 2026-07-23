"""Add model-governance persistence tables for candidate registry and lifecycle actions."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_model_governance"
down_revision: str | Sequence[str] | None = "0005_personality_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_candidates",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("candidate_alias", sa.String(length=255), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("scoring_policy_version", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_alias ~ '^.{1,255}$'",
            name="ck_model_candidate_alias",
        ),
        sa.CheckConstraint(
            "candidate_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_candidate_digest",
        ),
        sa.CheckConstraint(
            "scoring_policy_version ~ '^.{1,64}$'",
            name="ck_model_candidate_scoring_policy",
        ),
        sa.CheckConstraint(
            "requested_by ~ '^.{1,128}$'",
            name="ck_model_candidate_requester",
        ),
        sa.PrimaryKeyConstraint("owner_id", "candidate_alias"),
    )
    op.create_index("ix_model_candidates_owner", "model_candidates", ["owner_id"])
    op.create_index("ix_model_candidates_last_seen", "model_candidates", ["last_seen_at"])

    op.create_table(
        "model_benchmark_suites",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("suite_version", sa.String(length=32), nullable=False),
        sa.Column("scoring_policy_version", sa.String(length=32), nullable=False),
        sa.Column(
            "target_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("regression_tolerance", sa.Float(), nullable=False),
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
            "suite_version ~ '^.{1,32}$'",
            name="ck_model_benchmark_suite_version",
        ),
        sa.CheckConstraint(
            "scoring_policy_version ~ '^.{1,32}$'",
            name="ck_model_benchmark_suite_policy_version",
        ),
        sa.CheckConstraint(
            "minimum_sample_size BETWEEN 1 AND 1000000",
            name="ck_model_benchmark_suite_min_sample",
        ),
        sa.CheckConstraint(
            "regression_tolerance BETWEEN 0.0 AND 1.0",
            name="ck_model_benchmark_suite_regression_tolerance",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(target_metrics) = 'array'",
            name="ck_model_benchmark_suite_metrics_array",
        ),
        sa.CheckConstraint(
            "jsonb_array_length(target_metrics) > 0",
            name="ck_model_benchmark_suite_metrics_nonempty",
        ),
        sa.PrimaryKeyConstraint("owner_id", "suite_id"),
    )
    op.create_index(
        "ix_model_benchmark_suites_owner",
        "model_benchmark_suites",
        ["owner_id"],
    )
    op.create_index(
        "ix_model_benchmark_suites_version",
        "model_benchmark_suites",
        ["suite_version"],
    )

    op.create_table(
        "model_benchmark_runs",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("suite_version", sa.String(length=32), nullable=False),
        sa.Column("candidate_alias", sa.String(length=255), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("latency_ms_p95", sa.Float(), nullable=False),
        sa.Column("memory_mb_p95", sa.Float(), nullable=False),
        sa.Column("context_overlap", sa.Float(), nullable=False),
        sa.Column("failure_rate", sa.Float(), nullable=False),
        sa.Column("hardware_profile", sa.String(length=255), nullable=False),
        sa.Column("raw_measurements_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "sample_size BETWEEN 1 AND 1000000",
            name="ck_model_benchmark_runs_sample_size",
        ),
        sa.CheckConstraint(
            "quality_score BETWEEN 0.0 AND 1.0",
            name="ck_model_benchmark_runs_quality",
        ),
        sa.CheckConstraint(
            "latency_ms_p95 >= 0.0",
            name="ck_model_benchmark_runs_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "memory_mb_p95 >= 0.0",
            name="ck_model_benchmark_runs_memory_nonnegative",
        ),
        sa.CheckConstraint(
            "context_overlap BETWEEN 0.0 AND 1.0",
            name="ck_model_benchmark_runs_overlap_range",
        ),
        sa.CheckConstraint(
            "failure_rate BETWEEN 0.0 AND 1.0",
            name="ck_model_benchmark_runs_failure_range",
        ),
        sa.CheckConstraint(
            "raw_measurements_count >= 0",
            name="ck_model_benchmark_runs_measurements_nonnegative",
        ),
        sa.CheckConstraint(
            "hardware_profile IS NOT NULL",
            name="ck_model_benchmark_runs_hardware_profile",
        ),
        sa.PrimaryKeyConstraint("owner_id", "run_id"),
    )
    op.create_index(
        "ix_model_benchmark_runs_suite",
        "model_benchmark_runs",
        ["owner_id", "suite_id"],
    )
    op.create_index(
        "ix_model_benchmark_runs_candidate",
        "model_benchmark_runs",
        ["owner_id", "candidate_alias"],
    )

    op.create_table(
        "model_promotion_proposals",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("suite_id", sa.Uuid(), nullable=False),
        sa.Column("suite_version", sa.String(length=32), nullable=False),
        sa.Column("benchmark_run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_alias", sa.String(length=255), nullable=False),
        sa.Column("candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("incumbent_alias", sa.String(length=255), nullable=False),
        sa.Column("incumbent_digest", sa.String(length=64), nullable=False),
        sa.Column("decision_digest", sa.String(length=64), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "suite_version ~ '^.{1,32}$'",
            name="ck_model_promotion_suite_version",
        ),
        sa.CheckConstraint(
            "minimum_sample_size >= 1",
            name="ck_model_promotion_min_sample",
        ),
        sa.CheckConstraint(
            "decision_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_promotion_decision_digest",
        ),
        sa.CheckConstraint(
            "length(decision_reason) > 0",
            name="ck_model_promotion_decision_reason",
        ),
        sa.CheckConstraint(
            "incumbent_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_promotion_incumbent_digest",
        ),
        sa.PrimaryKeyConstraint("owner_id", "run_id"),
    )
    op.create_index("ix_model_promotion_owner", "model_promotion_proposals", ["owner_id"])

    op.create_table(
        "model_governance_state",
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("active_chat_alias", sa.String(length=255), nullable=True),
        sa.Column("active_chat_digest", sa.String(length=64), nullable=True),
        sa.Column("active_generation", sa.Integer(), nullable=False),
        sa.Column("prior_generation_anchor", sa.String(length=128), nullable=False),
        sa.Column("rollback_target_alias", sa.String(length=255), nullable=True),
        sa.Column("rollback_target_digest", sa.String(length=64), nullable=True),
        sa.Column("scoring_policy_version", sa.String(length=64), nullable=False),
        sa.Column("benchmarking_policy_version", sa.String(length=64), nullable=False),
        sa.Column("minimum_sample_size", sa.Integer(), nullable=False),
        sa.Column("promotion_quality_threshold", sa.Float(), nullable=False),
        sa.Column("rollback_quality_threshold", sa.Float(), nullable=False),
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
            "active_generation > 0",
            name="ck_model_governance_state_active_generation_positive",
        ),
        sa.CheckConstraint(
            "minimum_sample_size BETWEEN 1 AND 1000000",
            name="ck_model_governance_state_min_sample",
        ),
        sa.CheckConstraint(
            "promotion_quality_threshold BETWEEN 0.0 AND 1.0",
            name="ck_model_governance_state_promotion_quality_threshold",
        ),
        sa.CheckConstraint(
            "rollback_quality_threshold BETWEEN 0.0 AND 1.0",
            name="ck_model_governance_state_rollback_quality_threshold",
        ),
        sa.CheckConstraint(
            "prior_generation_anchor IS NOT NULL",
            name="ck_model_governance_state_prior_generation_anchor",
        ),
        sa.CheckConstraint(
            "scoring_policy_version ~ '^.{1,64}$'",
            name="ck_model_governance_state_scoring_policy",
        ),
        sa.CheckConstraint(
            "benchmarking_policy_version ~ '^.{1,64}$'",
            name="ck_model_governance_state_benchmarking_policy",
        ),
        sa.PrimaryKeyConstraint("owner_id"),
    )

    op.create_table(
        "model_activation_history",
        sa.Column("history_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.String(length=255), nullable=False),
        sa.Column("action_scope", sa.String(length=50), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("from_alias", sa.String(length=255), nullable=False),
        sa.Column("from_digest", sa.String(length=64), nullable=False),
        sa.Column("to_alias", sa.String(length=255), nullable=False),
        sa.Column("to_digest", sa.String(length=64), nullable=False),
        sa.Column("applied_generation", sa.Integer(), nullable=False),
        sa.Column("previous_generation", sa.Integer(), nullable=False),
        sa.Column("prior_generation_anchor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action_scope = 'chat_model'",
            name="ck_model_activation_scope_chat",
        ),
        sa.CheckConstraint(
            "action_type IN ('activation', 'rollback')",
            name="ck_model_activation_action_type",
        ),
        sa.CheckConstraint(
            "from_alias IS NOT NULL AND to_alias IS NOT NULL",
            name="ck_model_activation_aliases_present",
        ),
        sa.CheckConstraint(
            "from_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_activation_from_digest",
        ),
        sa.CheckConstraint(
            "to_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_activation_to_digest",
        ),
        sa.CheckConstraint(
            "applied_generation > 0",
            name="ck_model_activation_applied_generation",
        ),
        sa.CheckConstraint(
            "previous_generation > 0",
            name="ck_model_activation_previous_generation",
        ),
        sa.CheckConstraint(
            "from_digest IS NOT NULL",
            name="ck_model_activation_from_digest_required",
        ),
        sa.PrimaryKeyConstraint("history_id"),
    )
    op.create_index(
        "ix_model_activation_owner_created",
        "model_activation_history",
        ["owner_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_activation_owner_created",
        table_name="model_activation_history",
    )
    op.drop_table("model_activation_history")
    op.drop_table("model_governance_state")
    op.drop_index("ix_model_promotion_owner", table_name="model_promotion_proposals")
    op.drop_table("model_promotion_proposals")
    op.drop_index(
        "ix_model_benchmark_runs_candidate",
        table_name="model_benchmark_runs",
    )
    op.drop_index(
        "ix_model_benchmark_runs_suite",
        table_name="model_benchmark_runs",
    )
    op.drop_table("model_benchmark_runs")
    op.drop_index(
        "ix_model_benchmark_suites_version",
        table_name="model_benchmark_suites",
    )
    op.drop_index(
        "ix_model_benchmark_suites_owner",
        table_name="model_benchmark_suites",
    )
    op.drop_table("model_benchmark_suites")
    op.drop_index("ix_model_candidates_last_seen", table_name="model_candidates")
    op.drop_index("ix_model_candidates_owner", table_name="model_candidates")
    op.drop_table("model_candidates")
