from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mongars.db.models import MemoryDocument, TaskQueue
from mongars.memory.repository import MemoryHit
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
    kind: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any]
    priority: int = Field(default=100, ge=0, le=1000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    dedupe_key: str | None = Field(default=None, min_length=1, max_length=255)


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


class MemorySearchRequest(ApiModel):
    query: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=8, ge=1, le=50)
    mode: Literal["semantic", "hybrid"] = "hybrid"


class MemorySearchHit(ApiModel):
    chunk_id: UUID
    document_id: UUID
    score: float
    text: str
    source_uri: str | None
    title: str | None

    @classmethod
    def from_hit(cls, hit: MemoryHit) -> MemorySearchHit:
        return cls(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            score=hit.score,
            text=hit.text,
            source_uri=hit.source_uri,
            title=hit.title,
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
