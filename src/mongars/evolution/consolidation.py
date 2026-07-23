from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from .gap_detection import SchedulerFinding


@dataclass(frozen=True, slots=True)
class SchedulerProposal:
    """An auditable owner-scoped maintenance proposal payload."""

    owner_id: str
    operation_id: str
    evidence: tuple[tuple[str, Any], ...]
    confidence: float
    scope: str
    required_follow_up_action: str
    discovered_at: datetime

    def as_task_payload(self) -> dict[str, object]:
        return {
            "owner_id": self.owner_id,
            "operation_id": self.operation_id,
            "evidence": [
                {"key": key, "value": value}
                for key, value in self.evidence
                if isinstance(key, str)
            ],
            "confidence": round(self.confidence, 4),
            "scope": self.scope,
            "required_follow_up_action": self.required_follow_up_action,
            "discovered_at": self.discovered_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SchedulerProposalQueueRecord:
    """Auditable queue record for a proposal emission batch."""

    emitted_at: datetime
    proposal_count: int
    owner_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]
    proposal_cooldown_minutes: int

    def as_task_payload(self) -> dict[str, Any]:
        return {
            "emitted_at": self.emitted_at.isoformat(),
            "proposal_count": self.proposal_count,
            "owner_ids": list(self.owner_ids),
            "operation_ids": list(self.operation_ids),
            "proposal_cooldown_minutes": self.proposal_cooldown_minutes,
            "queue_digest": sha256(
                (
                    f"owner_ids:{','.join(self.owner_ids)}|operation_ids:{','.join(self.operation_ids)}|"
                    f"proposal_count:{self.proposal_count}"
                ).encode("utf-8")
            ).hexdigest(),
        }


def consolidate_findings(
    *,
    findings: tuple[SchedulerFinding, ...],
    owner_cooldowns: dict[str, datetime],
    cooldown_minutes: int = 30,
    max_proposals_per_owner: int = 20,
) -> tuple[SchedulerProposal, ...]:
    """Build deterministic proposals with owner-scoped cooldowns."""
    if max_proposals_per_owner <= 0:
        raise ValueError("max_proposals_per_owner must be positive")
    if cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be non-negative")
    now = datetime.now(UTC)
    proposals: list[SchedulerProposal] = []
    for finding in findings:
        if finding.owner_id == "":
            continue
        key = _cooldown_key(finding)
        next_allowed = owner_cooldowns.get(key)
        if next_allowed is not None and next_allowed > now:
            continue
        operation_id = f"sommeil-proposal:{finding.finding_type}:{finding.scope}"
        proposals.append(
            SchedulerProposal(
                owner_id=finding.owner_id,
                operation_id=operation_id,
                evidence=finding.evidence,
                confidence=finding.confidence,
                scope=finding.scope,
                required_follow_up_action=finding.required_follow_up_action,
                discovered_at=finding.discovered_at,
            )
        )
    proposals.sort(key=lambda item: (-item.confidence, item.scope))
    return tuple(proposals[:max_proposals_per_owner])


def emit_scheduler_proposals(
    *,
    findings: tuple[SchedulerFinding, ...],
    owner_cooldowns: dict[str, datetime],
    cooldown_minutes: int = 30,
    max_proposals_per_owner: int = 20,
    emitted_at: datetime | None = None,
) -> tuple[tuple[SchedulerProposal, ...], SchedulerProposalQueueRecord]:
    """Build proposals and an explicit auditable queue record.

    The function is purely functional: it does not mutate inputs or proposal records
    other than returning the durable queue payload that can be appended to an
    auditable proposal queue.
    """

    proposals = consolidate_findings(
        findings=findings,
        owner_cooldowns=owner_cooldowns,
        cooldown_minutes=cooldown_minutes,
        max_proposals_per_owner=max_proposals_per_owner,
    )
    event_time = emitted_at or datetime.now(UTC)
    owner_ids = tuple(dict.fromkeys((proposal.owner_id for proposal in proposals)))
    operation_ids = tuple(proposal.operation_id for proposal in proposals)
    queue_record = SchedulerProposalQueueRecord(
        emitted_at=event_time,
        proposal_count=len(proposals),
        owner_ids=owner_ids,
        operation_ids=operation_ids,
        proposal_cooldown_minutes=cooldown_minutes,
    )
    return proposals, queue_record


def _cooldown_key(finding: SchedulerFinding) -> str:
    return f"{finding.owner_id}:{finding.finding_type}:{finding.scope}"
