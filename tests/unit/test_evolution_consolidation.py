from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mongars.evolution.consolidation import (
    consolidate_findings,
    emit_scheduler_proposals,
)
from mongars.evolution.gap_detection import SchedulerFinding


def test_consolidation_filters_by_cooldown_and_keeps_evidence() -> None:
    now = datetime.now(UTC)
    findings = (
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="runtime_stale",
            scope="worker_heartbeat",
            confidence=0.9,
            evidence=(("age_seconds", "120"),),
            required_follow_up_action="review_worker_freshness",
            discovered_at=now,
        ),
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="legacy_embedding_coverage",
            scope="memory_embedding_coverage",
            confidence=0.6,
            evidence=(("legacy_chunk_count", "12"),),
            required_follow_up_action="queue_memory_reindex_proposal",
            discovered_at=now,
        ),
    )

    active_cooldowns = {
        "owner-1:runtime_stale:worker_heartbeat": now + timedelta(minutes=10),
    }
    proposals = consolidate_findings(
        findings=findings,
        owner_cooldowns=active_cooldowns,
        cooldown_minutes=30,
        max_proposals_per_owner=10,
    )

    assert len(proposals) == 1
    assert proposals[0].operation_id == "sommeil-proposal:legacy_embedding_coverage:memory_embedding_coverage"
    assert proposals[0].confidence == 0.6


def test_consolidation_limits_proposals_per_owner() -> None:
    findings = tuple(
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="runtime_stale",
            scope=f"worker_heartbeat_{index}",
            confidence=0.5 + index * 0.01,
            evidence=(("age_seconds", str(100 + index)),),
            required_follow_up_action="review_worker_freshness",
            discovered_at=datetime.now(UTC),
        )
        for index in range(5)
    )
    proposals = consolidate_findings(
        findings=findings,
        owner_cooldowns={},
        cooldown_minutes=0,
        max_proposals_per_owner=2,
    )
    assert len(proposals) == 2


def test_emit_scheduler_proposals_is_pure_and_records_only_queue_state() -> None:
    discovered_at = datetime.now(UTC)
    findings = (
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="runtime_stale",
            scope="worker_heartbeat",
            confidence=0.9,
            evidence=(("age_seconds", 120),),
            required_follow_up_action="review_worker_freshness",
            discovered_at=discovered_at,
        ),
        SchedulerFinding(
            owner_id="owner-2",
            finding_type="missing_provenance_metadata",
            scope="memory_documents",
            confidence=0.8,
            evidence=(("missing_document_count", 4),),
            required_follow_up_action="repair_document_provenance",
            discovered_at=discovered_at,
        ),
    )
    snapshot = findings
    emitted_at = datetime.now(UTC)

    proposals, queue_record = emit_scheduler_proposals(
        findings=findings,
        owner_cooldowns={},
        cooldown_minutes=42,
        max_proposals_per_owner=10,
        emitted_at=emitted_at,
    )

    assert findings == snapshot
    assert tuple(proposal.operation_id for proposal in proposals) == queue_record.operation_ids
    assert queue_record.proposal_count == len(proposals)
    assert queue_record.proposal_count == 2
    assert queue_record.owner_ids == ("owner-1", "owner-2")
    assert queue_record.proposal_cooldown_minutes == 42
    assert queue_record.emitted_at == emitted_at

    payload = queue_record.as_task_payload()
    assert payload["proposal_count"] == 2
    assert payload["owner_ids"] == ["owner-1", "owner-2"]
    assert payload["operation_ids"] == [
        "sommeil-proposal:runtime_stale:worker_heartbeat",
        "sommeil-proposal:missing_provenance_metadata:memory_documents",
    ]
    assert payload["proposal_cooldown_minutes"] == 42
    assert "queue_digest" in payload
    assert isinstance(payload["queue_digest"], str)
