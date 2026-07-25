from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mongars.db.models import MemoryDocument, TaskQueue
from mongars.memory.repository import MemoryHit
from mongars.rm.contracts import TaskKind
from mongars.orchestrator.personality import (
    PersonalityDimension,
    PersonalityPreference,
    PersonalitySource,
    PersonalitySnapshot,
)
from mongars.rm.payload_view import (
    TaskPayloadPage as RenderedTaskPayloadPage,
)
from mongars.rm.payload_view import (
    TaskPayloadSummary as RenderedTaskPayloadSummary,
)
from mongars.rm.payload_view import (
    summarize_task_payload,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class P2PMetadataRequest(ApiModel):
    schema_version: str = Field(min_length=1, max_length=64)
    sensitivity: str = Field(min_length=1, max_length=64)
    retention_class: str = Field(min_length=1, max_length=64)
    trust: str = Field(min_length=1, max_length=32)
    source_time: datetime


class P2PPairRequest(ApiModel):
    peer_id: str = Field(min_length=1, max_length=255)
    key_id: str = Field(min_length=1, max_length=255)
    signing_secret_b64: str = Field(min_length=8, max_length=2048)


class P2PPairResponse(ApiModel):
    owner_id: str
    peer_id: str
    key_id: str
    paired: bool = True


class P2PStatusResponse(ApiModel):
    owner_id: str
    paired_peers: int
    paired_keys: int
    quarantine_item_count: int
    quarantine_used_bytes: int


class P2PEnvelopeExportRequest(ApiModel):
    envelope_id: str = Field(min_length=1, max_length=255)
    sender_peer_id: str = Field(min_length=1, max_length=255)
    recipient_peer_id: str = Field(min_length=1, max_length=255)
    sender_key_id: str = Field(min_length=1, max_length=255)
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]
    schema_version: str = Field(min_length=1, max_length=64)
    sensitivity: str = Field(min_length=1, max_length=64)
    retention_class: str = Field(min_length=1, max_length=64)
    trust: str = Field(min_length=1, max_length=32)
    source_time: datetime
    signing_secret_b64: str = Field(min_length=8, max_length=2048)


class P2PEnvelopeExportResponse(ApiModel):
    envelope_id: str
    sender_peer_id: str
    recipient_peer_id: str
    owner_id: str
    sender_key_id: str
    issued_at: datetime
    expires_at: datetime
    nonce: str
    protocol_version: int
    schema_version: str
    sensitivity: str
    retention_class: str
    trust: str
    source_time: datetime
    payload_sha256: str
    payload_bytes: str
    signature: str


class P2PEnvelopeImportRequest(ApiModel):
    envelope_id: str = Field(min_length=1, max_length=255)
    sender_peer_id: str = Field(min_length=1, max_length=255)
    recipient_peer_id: str = Field(min_length=1, max_length=255)
    owner_id: str = Field(min_length=1, max_length=255)
    sender_key_id: str = Field(min_length=1, max_length=255)
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(min_length=1, max_length=255)
    payload: dict[str, Any]
    schema_version: str = Field(min_length=1, max_length=64)
    sensitivity: str = Field(min_length=1, max_length=64)
    retention_class: str = Field(min_length=1, max_length=64)
    trust: str = Field(min_length=1, max_length=32)
    source_time: datetime
    signature: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    payload_bytes: str | None = None


class P2PEnvelopeImportResponse(ApiModel):
    envelope_id: str
    envelope_sha256: str
    reviewed: bool
    created: bool
    quarantine_item_count: int


class ChatRequest(ApiModel):
    session_id: UUID | None = None
    message: str = Field(min_length=1)
    require_local_only: bool = True
    web_search: Literal["off", "auto", "required"] = "auto"


class WebSource(ApiModel):
    title: str
    url: str


class ChatResponse(ApiModel):
    trace_id: str
    session_id: UUID
    status: Literal["ok"] = "ok"
    answer: str
    model: str
    memory_hits: int
    web_search_status: Literal[
        "not_requested",
        "ok",
        "disabled",
        "unavailable",
        "no_results",
        "context_limited",
    ]
    sources: list[WebSource]


class TaskCreateRequest(ApiModel):
    kind: TaskKind
    payload: dict[str, Any]
    priority: int = Field(default=100, ge=0, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=255)


class ExplicitFeedbackCreateCorrectionRequest(ApiModel):
    kind: Annotated[Literal["correction"], "correction"]
    feedback_id: UUID
    response_trace_id: str = Field(pattern=r"^trc_[0-9a-f]{32}$")
    correction_text: str = Field(min_length=1, max_length=2_000)


class ExplicitFeedbackCreateHelpfulnessRequest(ApiModel):
    kind: Annotated[Literal["helpfulness"], "helpfulness"]
    feedback_id: UUID
    response_trace_id: str = Field(pattern=r"^trc_[0-9a-f]{32}$")
    helpful: bool


class ExplicitFeedbackCreatePreferenceRequest(ApiModel):
    kind: Annotated[Literal["preference"], "preference"]
    feedback_id: UUID
    response_trace_id: str | None = Field(default=None, pattern=r"^trc_[0-9a-f]{32}$")
    dimension: PersonalityDimension
    desired_value: float = Field(ge=0.0, le=1.0)


ExplicitFeedbackCreateRequest = Annotated[
    ExplicitFeedbackCreateCorrectionRequest
    | ExplicitFeedbackCreateHelpfulnessRequest
    | ExplicitFeedbackCreatePreferenceRequest,
    Field(discriminator="kind"),
]


class TaskApproveRequest(ApiModel):
    action_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class TaskResponse(ApiModel):
    id: UUID
    kind: str
    risk_level: str
    status: str
    trace_id: str
    priority: int
    attempt_count: int
    max_attempts: int
    result: dict[str, Any] | None
    error_text: str | None
    approval_expires_at: datetime | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, task: TaskQueue) -> TaskResponse:
        return cls(
            id=task.id,
            kind=task.kind,
            risk_level=task.risk_level,
            status=task.status,
            trace_id=task.trace_id,
            priority=task.priority,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            result=task.result,
            error_text=task.error_text,
            approval_expires_at=task.approval_expires_at,
            approved_at=task.approved_at,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )


class DocumentUploadResponse(ApiModel):
    id: UUID
    kind: Literal["document.ingest"] = "document.ingest"
    status: Literal["waiting_approval"] = "waiting_approval"
    risk_level: Literal["local_mutation"] = "local_mutation"
    action_digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_model(cls, task: TaskQueue) -> DocumentUploadResponse:
        if (
            task.kind != "document.ingest"
            or task.status != "waiting_approval"
            or task.risk_level != "local_mutation"
            or task.action_digest is None
        ):
            raise ValueError("document upload task is not in its expected approval state")
        return cls(id=task.id, action_digest=task.action_digest)


class TaskPayloadSummary(ApiModel):
    format: Literal["sorted-pretty-json-v1"] = "sorted-pretty-json-v1"
    encoding: Literal["utf-8"] = "utf-8"
    byte_length: int
    character_count: int
    page_count: int
    page_size_characters: int
    top_level_field_count: int
    preview_head: str
    preview_tail: str
    preview_omitted_characters: int

    @classmethod
    def from_rendered(cls, summary: RenderedTaskPayloadSummary) -> TaskPayloadSummary:
        return cls(**asdict(summary))


class TaskPayloadPageResponse(ApiModel):
    task_id: UUID
    action_digest: str | None
    format: Literal["sorted-pretty-json-v1"] = "sorted-pretty-json-v1"
    encoding: Literal["utf-8"] = "utf-8"
    page_index: int
    page_count: int
    page_size_characters: int
    character_start: int
    character_end: int
    content: str

    @classmethod
    def from_rendered(
        cls,
        *,
        task: TaskQueue,
        page: RenderedTaskPayloadPage,
    ) -> TaskPayloadPageResponse:
        return cls(
            task_id=task.id,
            action_digest=task.action_digest,
            **asdict(page),
        )


class TaskDetailResponse(TaskResponse):
    payload_summary: TaskPayloadSummary
    action_digest: str | None

    @classmethod
    def from_model(cls, task: TaskQueue) -> TaskDetailResponse:
        return cls(
            id=task.id,
            kind=task.kind,
            risk_level=task.risk_level,
            status=task.status,
            trace_id=task.trace_id,
            priority=task.priority,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            result=task.result,
            error_text=task.error_text,
            approval_expires_at=task.approval_expires_at,
            approved_at=task.approved_at,
            created_at=task.created_at,
            updated_at=task.updated_at,
            payload_summary=TaskPayloadSummary.from_rendered(summarize_task_payload(task.payload)),
            action_digest=task.action_digest,
        )


class ExplicitFeedbackCreateResponse(ApiModel):
    feedback_id: UUID
    kind: Literal["correction", "helpfulness", "preference"]
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool
    applied_task_id: UUID | None
    applied_revision: int | None
    proposal: dict[str, Any] | None = None


class ProfileApplyFromFeedbackRequest(ApiModel):
    feedback_id: UUID


class PersonalityPreferenceResponse(ApiModel):
    dimension: PersonalityDimension
    value: float
    confidence: float
    evidence_count: int

    @classmethod
    def from_model(cls, preference: PersonalityPreference) -> PersonalityPreferenceResponse:
        return cls(
            dimension=preference.dimension,
            value=preference.value,
            confidence=preference.confidence,
            evidence_count=preference.evidence_count,
        )


class PersonalitySnapshotResponse(ApiModel):
    revision: int
    source: PersonalitySource
    profile_digest: str | None = None
    schema_version: str = "personality-v1"
    preferences: tuple[PersonalityPreferenceResponse, ...]

    @classmethod
    def from_model(cls, snapshot: PersonalitySnapshot) -> PersonalitySnapshotResponse:
        return cls(
            revision=snapshot.revision,
            source=snapshot.source,
            profile_digest=snapshot.profile_digest,
            schema_version=snapshot.schema_version,
            preferences=tuple(
                PersonalityPreferenceResponse.from_model(preference)
                for preference in snapshot.preferences
            ),
        )


class PersonalityRevisionResponse(ApiModel):
    feedback_id: UUID
    feedback_digest: str
    proposal_digest: str
    task_id: UUID
    changed_dimension: PersonalityDimension
    conflict: bool
    created_at: datetime
    snapshot: PersonalitySnapshotResponse


class PersonalityHistoryResponse(ApiModel):
    items: tuple[PersonalityRevisionResponse, ...]


class PersonalityExportResponse(ApiModel):
    exported_at: datetime
    current: PersonalitySnapshotResponse
    history: tuple[PersonalityRevisionResponse, ...]

class MemorySearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=8, ge=1, le=50)
    mode: Literal["semantic", "hybrid"] = "hybrid"

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace text")
        return value


class MemoryReindexRequest(ApiModel):
    document_id: UUID | None = None
    batch_size: int = Field(default=32, ge=1, le=128)


class MemorySearchHit(ApiModel):
    chunk_id: UUID
    document_id: UUID
    score: float
    text: str
    source_uri: str | None
    title: str | None
    locator: dict[str, Any]

    @classmethod
    def from_hit(cls, hit: MemoryHit) -> MemorySearchHit:
        return cls(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            score=hit.score,
            text=hit.text,
            source_uri=hit.source_uri,
            title=hit.title,
            locator=hit.locator,
        )


class MemorySearchResponse(ApiModel):
    hits: list[MemorySearchHit]


class MemoryDocumentCreateRequest(ApiModel):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=500)
    sensitivity: Literal["private", "shared", "restricted"] = "private"
    retention_class: Literal["keep", "ttl_30d", "ttl_90d", "legal_hold"] = "keep"


class MemoryDocumentResponse(ApiModel):
    id: UUID
    source_type: str
    source_uri: str | None
    title: str | None
    mime_type: str | None
    sensitivity: str
    retention_class: str
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]

    @classmethod
    def from_model(cls, document: MemoryDocument) -> MemoryDocumentResponse:
        return cls(
            id=document.id,
            source_type=document.source_type,
            source_uri=document.source_uri,
            title=document.title,
            mime_type=document.mime_type,
            sensitivity=document.sensitivity,
            retention_class=document.retention_class,
            expires_at=document.expires_at,
            created_at=document.created_at,
            updated_at=document.updated_at,
            metadata=document.metadata_json,
        )
