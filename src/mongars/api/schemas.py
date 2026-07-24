"""Public API schema facade and complete explicit-feedback response contract."""

# ruff: noqa: F401,F403

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from mongars.api._schemas import *
from mongars.api._schemas import ApiModel, PersonalitySnapshotResponse, TaskResponse


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
