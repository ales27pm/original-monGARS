from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Literal

FindingType = Literal[
    "repeated_task_failures",
    "runtime_stale",
    "unresolved_contradiction",
    "legacy_embedding_coverage",
    "missing_provenance_metadata",
]
SchedulerFindingType = FindingType


@dataclass(frozen=True, slots=True)
class SchedulerFinding:
    """One bounded signal for the proposal scheduler."""

    owner_id: str
    finding_type: FindingType
    scope: str
    confidence: float
    evidence: tuple[tuple[str, Any], ...]
    required_follow_up_action: str
    discovered_at: datetime

    def as_task_payload(self) -> dict[str, object]:
        return {
            "owner_id": self.owner_id,
            "finding_type": self.finding_type,
            "scope": self.scope,
            "confidence": round(self.confidence, 4),
            "evidence": [
                {"key": key, "value": value}
                for key, value in self.evidence
                if isinstance(key, str)
            ],
            "required_follow_up_action": self.required_follow_up_action,
            "discovered_at": self.discovered_at.isoformat(),
        }


def _normalize_evidence(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def detect_repeated_task_failures(
    owner_id: str,
    *,
    consecutive_failures: int,
    threshold: int = 3,
) -> SchedulerFinding | None:
    if consecutive_failures < threshold:
        return None
    if threshold <= 0:
        raise ValueError("threshold must be positive")
    confidence = min(1.0, consecutive_failures / float(max(1, threshold * 2)))
    return SchedulerFinding(
        owner_id=owner_id,
        finding_type="repeated_task_failures",
        scope="task_queue",
        confidence=confidence,
        evidence=(
            ("consecutive_failures", consecutive_failures),
            ("threshold", threshold),
        ),
        required_follow_up_action="evaluate_task_failure_pattern",
        discovered_at=datetime.now(UTC),
    )


def detect_runtime_staleness_gap(
    owner_id: str,
    *,
    age_seconds: float,
    stale_threshold_seconds: int,
) -> SchedulerFinding | None:
    if stale_threshold_seconds <= 0:
        raise ValueError("stale_threshold_seconds must be positive")
    if age_seconds < stale_threshold_seconds:
        return None
    confidence = min(1.0, age_seconds / (stale_threshold_seconds * 2))
    return SchedulerFinding(
        owner_id=owner_id,
        finding_type="runtime_stale",
        scope="worker_heartbeat",
        confidence=confidence,
        evidence=(
            ("age_seconds", _normalize_evidence(age_seconds)),
            ("stale_threshold_seconds", stale_threshold_seconds),
        ),
        required_follow_up_action="review_worker_freshness",
        discovered_at=datetime.now(UTC),
    )


def detect_unresolved_contradiction_gap(
    owner_id: str,
    *,
    contradiction_count: int,
) -> SchedulerFinding | None:
    if contradiction_count <= 0:
        return None
    confidence = min(1.0, 0.25 + contradiction_count / 10)
    return SchedulerFinding(
        owner_id=owner_id,
        finding_type="unresolved_contradiction",
        scope="personality_profile",
        confidence=confidence,
        evidence=(("contradiction_count", contradiction_count),),
        required_follow_up_action="review_profile_conflict_history",
        discovered_at=datetime.now(UTC),
    )


def detect_legacy_embedding_coverage_gap(
    owner_id: str,
    *,
    legacy_chunk_count: int,
) -> SchedulerFinding | None:
    if legacy_chunk_count <= 0:
        return None
    confidence = min(1.0, legacy_chunk_count / 20)
    return SchedulerFinding(
        owner_id=owner_id,
        finding_type="legacy_embedding_coverage",
        scope="memory_embedding_coverage",
        confidence=confidence,
        evidence=(("legacy_chunk_count", legacy_chunk_count),),
        required_follow_up_action="queue_memory_reindex_proposal",
        discovered_at=datetime.now(UTC),
    )


def detect_missing_provenance_gap(
    owner_id: str,
    *,
    missing_document_count: int,
) -> SchedulerFinding | None:
    if missing_document_count <= 0:
        return None
    confidence = min(1.0, 0.15 + missing_document_count / 25)
    return SchedulerFinding(
        owner_id=owner_id,
        finding_type="missing_provenance_metadata",
        scope="memory_documents",
        confidence=confidence,
        evidence=(("missing_document_count", missing_document_count),),
        required_follow_up_action="repair_document_provenance",
        discovered_at=datetime.now(UTC),
    )


def deduplicate_findings(
    *,
    findings: list[SchedulerFinding],
    max_per_owner: int = 20,
) -> tuple[SchedulerFinding, ...]:
    if max_per_owner <= 0:
        raise ValueError("max_per_owner must be positive")
    filtered: dict[tuple[str, FindingType, str], SchedulerFinding] = {}
    for finding in findings:
        if finding.confidence < 0.0 or finding.confidence > 1.0:
            raise ValueError("finding confidence must be within 0..1")
        key = (finding.owner_id, finding.finding_type, finding.scope)
        existing = filtered.get(key)
        if existing is None or finding.discovered_at > existing.discovered_at:
            filtered[key] = finding
    deduplicated = tuple(
        sorted(
            filtered.values(),
            key=lambda item: (item.owner_id, item.finding_type, item.scope),
        )
    )
    return deduplicated[:max_per_owner]
