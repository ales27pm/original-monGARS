from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from mongars.ids import uuid7


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MemoryDocument(TimestampMixin, Base):
    __tablename__ = "memory_documents"
    __table_args__ = (
        UniqueConstraint("owner_id", "source_sha256", name="uq_memory_document_owner_sha"),
        CheckConstraint(
            "sensitivity IN ('private', 'shared', 'restricted')",
            name="ck_memory_document_sensitivity",
        ),
        CheckConstraint(
            "retention_class IN ('keep', 'ttl_30d', 'ttl_90d', 'legal_hold')",
            name="ck_memory_document_retention",
        ),
        Index("ix_memory_documents_owner_created", "owner_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'private'")
    )
    retention_class: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'keep'")
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class MemoryChunk(Base):
    __tablename__ = "memory_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_memory_chunk_position"),
        Index("ix_memory_chunks_document", "document_id"),
        # Compatibility index retained during the expand phase of migration 0004.
        # New retrieval uses ``MemoryChunkEmbedding``; a later contract migration
        # removes this after rollback is no longer supported.
        Index(
            "ix_memory_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_memory_chunks_search_vector_gin",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("memory_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    locator: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    legacy_embedding: Mapped[list[float] | None] = mapped_column(
        "embedding",
        Vector(768),
        nullable=True,
    )
    legacy_embedding_model: Mapped[str | None] = mapped_column(
        "embedding_model",
        String(255),
        nullable=True,
    )
    plaintext: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, plaintext)", persisted=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoryChunkEmbedding(Base):
    """One immutable semantic-space representation of a durable memory chunk."""

    __tablename__ = "memory_chunk_embeddings"
    __table_args__ = (
        CheckConstraint("dimension = 768", name="ck_memory_chunk_embedding_dimension"),
        CheckConstraint(
            "normalization_policy IN ('none', 'l2')",
            name="ck_memory_chunk_embedding_normalization",
        ),
        CheckConstraint("truncate = false", name="ck_memory_chunk_embedding_no_truncation"),
        CheckConstraint(
            "maximum_input_bytes > 0",
            name="ck_memory_chunk_embedding_positive_input_limit",
        ),
        Index("ix_memory_chunk_embeddings_space", "embedding_space_id", "chunk_id"),
        Index(
            "ix_memory_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    chunk_id: Mapped[UUID] = mapped_column(
        ForeignKey("memory_chunks.id", ondelete="CASCADE"), primary_key=True
    )
    embedding_space_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    model_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    normalization_policy: Mapped[str] = mapped_column(String(20), nullable=False)
    document_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    query_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    clustering_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    classification_instruction: Mapped[str] = mapped_column(Text, nullable=False)
    truncate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    maximum_input_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoryDocumentProvenance(Base):
    """One immutable source observation for content-addressed memory.

    Content is deduplicated at ``MemoryDocument`` while each accepted submission keeps
    its own source and metadata here. The digest makes retries of the same submission
    idempotent without discarding genuinely new provenance.
    """

    __tablename__ = "memory_document_provenance"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "provenance_sha256",
            name="uq_memory_document_provenance_digest",
        ),
        Index("ix_memory_document_provenance_document", "document_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("memory_documents.id", ondelete="CASCADE"), nullable=False
    )
    provenance_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EpisodicEvent(Base):
    __tablename__ = "episodic_events"
    __table_args__ = (
        Index("ix_episodic_events_owner_created", "owner_id", text("created_at DESC")),
        Index("ix_episodic_events_trace", "trace_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    session_id: Mapped[UUID | None] = mapped_column(index=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskQueue(TimestampMixin, Base):
    __tablename__ = "task_queue"
    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_task_queue_id_owner"),
        CheckConstraint(
            "risk_level IN ('read_only', 'local_mutation', 'external_side_effect')",
            name="ck_task_queue_risk_level",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_approval', 'done', 'failed', 'cancelled')",
            name="ck_task_queue_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_task_queue_attempt_nonnegative"),
        CheckConstraint("max_attempts > 0", name="ck_task_queue_max_attempts_positive"),
        CheckConstraint("attempt_count <= max_attempts", name="ck_task_queue_attempt_within_limit"),
        CheckConstraint("priority BETWEEN 0 AND 1000", name="ck_task_queue_priority_range"),
        CheckConstraint(
            "((status = 'running') = (lease_expires_at IS NOT NULL)) AND "
            "((status = 'running') = (execution_token IS NOT NULL))",
            name="ck_task_queue_running_has_lease",
        ),
        CheckConstraint(
            "risk_level = 'read_only' OR "
            "(action_digest IS NOT NULL AND approval_expires_at IS NOT NULL)",
            name="ck_task_queue_privileged_has_digest",
        ),
        CheckConstraint(
            "risk_level = 'read_only' OR "
            "status IN ('waiting_approval', 'cancelled', 'failed') OR approved_at IS NOT NULL",
            name="ck_task_queue_privileged_execution_approved",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR approved_at IS NOT NULL",
            name="ck_task_queue_consumption_requires_approval",
        ),
        Index(
            "ix_task_queue_claim",
            "status",
            "run_after",
            text("priority DESC"),
            "created_at",
        ),
        Index("ix_task_queue_owner_created", "owner_id", text("created_at DESC")),
        Index(
            "uq_task_queue_dedupe",
            "owner_id",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    parent_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_queue.id", ondelete="SET NULL")
    )
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="100")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_token: Mapped[UUID | None] = mapped_column()
    dedupe_key: Mapped[str | None] = mapped_column(String(255))
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_text: Mapped[str | None] = mapped_column(Text)
    action_digest: Mapped[str | None] = mapped_column(String(64))
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentStaging(Base):
    """Bounded upload bytes awaiting an approved ``document.ingest`` task.

    The task payload contains only immutable review metadata and the content digest;
    raw bytes stay owner-scoped here and are never copied into task/event JSON.
    """

    __tablename__ = "document_staging"
    __table_args__ = (
        CheckConstraint("byte_size > 0", name="ck_document_staging_positive_size"),
        CheckConstraint(
            "byte_size <= 20000000",
            name="ck_document_staging_max_size",
        ),
        CheckConstraint(
            "octet_length(source_sha256) = 32",
            name="ck_document_staging_sha256_length",
        ),
        CheckConstraint(
            "octet_length(content) = byte_size",
            name="ck_document_staging_content_size",
        ),
        ForeignKeyConstraint(
            ("task_id", "owner_id"),
            ("task_queue.id", "task_queue.owner_id"),
            name="fk_document_staging_task_owner",
            ondelete="CASCADE",
        ),
        Index("ix_document_staging_owner_created", "owner_id", "created_at"),
        Index("ix_document_staging_expires", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    task_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    detected_mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class InferenceMetric(Base):
    """Small durable roll-up for local operations without an external metrics dependency."""

    __tablename__ = "inference_metrics"
    __table_args__ = (Index("ix_inference_metrics_created", text("created_at DESC")),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    backend: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(30), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RuntimeComponent(Base):
    """Latest durable heartbeat for a worker-owned runtime capability."""

    __tablename__ = "runtime_components"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'degraded', 'unhealthy')",
            name="ck_runtime_component_status",
        ),
        Index("ix_runtime_components_type_seen", "component_type", text("last_seen_at DESC")),
    )

    component_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    instance_id: Mapped[UUID] = mapped_column(nullable=False)
    component_type: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelCandidate(Base):
    """Registered chat-model candidates for governance operations."""

    __tablename__ = "model_candidates"
    __table_args__ = (
        CheckConstraint(
            "candidate_alias ~ '^.{1,255}$'",
            name="ck_model_candidate_alias",
        ),
        CheckConstraint(
            "candidate_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_candidate_digest",
        ),
        CheckConstraint(
            "scoring_policy_version ~ '^.{1,64}$'",
            name="ck_model_candidate_scoring_policy",
        ),
        CheckConstraint(
            "requested_by ~ '^.{1,128}$'",
            name="ck_model_candidate_requester",
        ),
        Index("ix_model_candidates_owner", "owner_id"),
        Index("ix_model_candidates_last_seen", "last_seen_at"),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    candidate_alias: Mapped[str] = mapped_column(String(255), primary_key=True)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelBenchmarkSuite(Base):
    """One benchmark suite definition used to validate proposed models."""

    __tablename__ = "model_benchmark_suites"
    __table_args__ = (
        CheckConstraint(
            "suite_version ~ '^.{1,32}$'",
            name="ck_model_benchmark_suite_version",
        ),
        CheckConstraint(
            "scoring_policy_version ~ '^.{1,32}$'",
            name="ck_model_benchmark_suite_policy_version",
        ),
        CheckConstraint("minimum_sample_size BETWEEN 1 AND 1000000", name="ck_model_benchmark_suite_min_sample"),
        CheckConstraint(
            "regression_tolerance BETWEEN 0.0 AND 1.0",
            name="ck_model_benchmark_suite_regression_tolerance",
        ),
        CheckConstraint(
            "jsonb_typeof(target_metrics) = 'array'",
            name="ck_model_benchmark_suite_metrics_array",
        ),
        CheckConstraint(
            "jsonb_array_length(target_metrics) > 0",
            name="ck_model_benchmark_suite_metrics_nonempty",
        ),
        Index("ix_model_benchmark_suites_owner", "owner_id"),
        Index("ix_model_benchmark_suites_version", "suite_version"),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    suite_id: Mapped[UUID] = mapped_column(primary_key=True)
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False)
    scoring_policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    target_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    regression_tolerance: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelBenchmarkRun(Base):
    """Benchmark result for one candidate model proposal."""

    __tablename__ = "model_benchmark_runs"
    __table_args__ = (
        CheckConstraint("sample_size BETWEEN 1 AND 1000000", name="ck_model_benchmark_runs_sample_size"),
        CheckConstraint("quality_score BETWEEN 0.0 AND 1.0", name="ck_model_benchmark_runs_quality"),
        CheckConstraint("latency_ms_p95 >= 0.0", name="ck_model_benchmark_runs_latency_nonnegative"),
        CheckConstraint("memory_mb_p95 >= 0.0", name="ck_model_benchmark_runs_memory_nonnegative"),
        CheckConstraint("context_overlap BETWEEN 0.0 AND 1.0", name="ck_model_benchmark_runs_overlap_range"),
        CheckConstraint("failure_rate BETWEEN 0.0 AND 1.0", name="ck_model_benchmark_runs_failure_range"),
        CheckConstraint("raw_measurements_count >= 0", name="ck_model_benchmark_runs_measurements_nonnegative"),
        CheckConstraint(
            "hardware_profile IS NOT NULL",
            name="ck_model_benchmark_runs_hardware_profile",
        ),
        Index("ix_model_benchmark_runs_suite", "owner_id", "suite_id"),
        Index("ix_model_benchmark_runs_candidate", "owner_id", "candidate_alias"),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    suite_id: Mapped[UUID] = mapped_column(nullable=False)
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    latency_ms_p95: Mapped[float] = mapped_column(Float, nullable=False)
    memory_mb_p95: Mapped[float] = mapped_column(Float, nullable=False)
    context_overlap: Mapped[float] = mapped_column(Float, nullable=False)
    failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    hardware_profile: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_measurements_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelPromotionProposal(Base):
    """Promotion decision emitted from governance policy execution."""

    __tablename__ = "model_promotion_proposals"
    __table_args__ = (
        CheckConstraint(
            "suite_version ~ '^.{1,32}$'",
            name="ck_model_promotion_suite_version",
        ),
        CheckConstraint(
            "minimum_sample_size >= 1",
            name="ck_model_promotion_min_sample",
        ),
        CheckConstraint(
            "decision_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_promotion_decision_digest",
        ),
        CheckConstraint(
            "decision_reason IS NOT NULL AND length(decision_reason) > 0",
            name="ck_model_promotion_decision_reason",
        ),
        CheckConstraint(
            "incumbent_digest IS NOT NULL",
            name="ck_model_promotion_incumbent_digest",
        ),
        Index("ix_model_promotion_owner", "owner_id"),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(primary_key=True)
    suite_id: Mapped[UUID] = mapped_column(nullable=False)
    suite_version: Mapped[str] = mapped_column(String(32), nullable=False)
    benchmark_run_id: Mapped[UUID] = mapped_column(nullable=False)
    candidate_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    incumbent_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    incumbent_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelGovernanceState(Base):
    """Durable runtime state for the current active chat model governance state."""

    __tablename__ = "model_governance_state"
    __table_args__ = (
        CheckConstraint(
            "active_generation > 0",
            name="ck_model_governance_state_active_generation_positive",
        ),
        CheckConstraint(
            "minimum_sample_size BETWEEN 1 AND 1000000",
            name="ck_model_governance_state_min_sample",
        ),
        CheckConstraint(
            "promotion_quality_threshold BETWEEN 0.0 AND 1.0",
            name="ck_model_governance_state_promotion_quality_threshold",
        ),
        CheckConstraint(
            "rollback_quality_threshold BETWEEN 0.0 AND 1.0",
            name="ck_model_governance_state_rollback_quality_threshold",
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    active_chat_alias: Mapped[str | None] = mapped_column(String(255))
    active_chat_digest: Mapped[str | None] = mapped_column(String(64))
    active_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_generation_anchor: Mapped[str] = mapped_column(String(128), nullable=False)
    rollback_target_alias: Mapped[str | None] = mapped_column(String(255))
    rollback_target_digest: Mapped[str | None] = mapped_column(String(64))
    scoring_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmarking_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    minimum_sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    promotion_quality_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    rollback_quality_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelActivationHistory(Base):
    """Durable audit trail for model activation and rollback actions."""

    __tablename__ = "model_activation_history"
    __table_args__ = (
        CheckConstraint(
            "action_scope = 'chat_model'",
            name="ck_model_activation_scope_chat",
        ),
        CheckConstraint(
            "action_type IN ('activation', 'rollback')",
            name="ck_model_activation_action_type",
        ),
        CheckConstraint(
            "from_alias IS NOT NULL AND to_alias IS NOT NULL",
            name="ck_model_activation_aliases_present",
        ),
        CheckConstraint(
            "from_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_activation_from_digest",
        ),
        CheckConstraint(
            "to_digest ~ '^[0-9a-f]{64}$'",
            name="ck_model_activation_to_digest",
        ),
        CheckConstraint(
            "applied_generation > 0",
            name="ck_model_activation_applied_generation",
        ),
        CheckConstraint(
            "previous_generation > 0",
            name="ck_model_activation_previous_generation",
        ),
        CheckConstraint(
            "from_digest IS NOT NULL",
            name="ck_model_activation_from_digest_required",
        ),
        Index("ix_model_activation_owner_created", "owner_id", text("created_at DESC")),
    )

    history_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    owner_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action_scope: Mapped[str] = mapped_column(String(50), nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    from_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    to_alias: Mapped[str] = mapped_column(String(255), nullable=False)
    to_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    applied_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    prior_generation_anchor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text)
    source_run_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
