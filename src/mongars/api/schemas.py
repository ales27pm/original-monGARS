"""Public API schema facade and complete explicit-feedback response contract."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from mongars.api._schemas import (
    ApiModel,
    ChatRequest,
    ChatResponse,
    DocumentUploadResponse,
    ExplicitFeedbackCreateCorrectionRequest,
    ExplicitFeedbackCreateHelpfulnessRequest,
    ExplicitFeedbackCreatePreferenceRequest,
    ExplicitFeedbackCreateRequest,
    MemoryDocumentCreateRequest,
    MemoryDocumentResponse,
    MemoryReindexRequest,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResponse,
    P2PEnvelopeExportRequest,
    P2PEnvelopeExportResponse,
    P2PEnvelopeImportRequest,
    P2PEnvelopeImportResponse,
    P2PMetadataRequest,
    P2PPairRequest,
    P2PPairResponse,
    P2PStatusResponse,
    PersonalityExportResponse,
    PersonalityHistoryResponse,
    PersonalityPreferenceResponse,
    PersonalityRevisionResponse,
    PersonalitySnapshotResponse,
    ProfileApplyFromFeedbackRequest,
    TaskApproveRequest,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskPayloadPageResponse,
    TaskPayloadSummary,
    TaskResponse,
    WebSource,
)


class ExplicitFeedbackCreateResponse(ApiModel):
    feedback_id: UUID
    kind: Literal["correction", "helpfulness", "preference"]
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool
    applied_task_id: UUID | None
    applied_revision: int | None
    proposal: dict[str, Any] | None = None
    profile: PersonalitySnapshotResponse
    proposal_task: TaskResponse | None = None
