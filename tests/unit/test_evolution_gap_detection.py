from __future__ import annotations

from datetime import UTC, datetime

from mongars.evolution.gap_detection import (
    SchedulerFinding,
    deduplicate_findings,
    detect_legacy_embedding_coverage_gap,
    detect_missing_provenance_gap,
    detect_repeated_task_failures,
    detect_runtime_staleness_gap,
    detect_unresolved_contradiction_gap,
)


def test_repeated_task_failures_yield_dedicated_detection() -> None:
    signal = detect_repeated_task_failures(
        owner_id="owner-1",
        consecutive_failures=4,
        threshold=3,
    )
    assert signal is not None
    assert signal.finding_type == "repeated_task_failures"
    assert signal.scope == "task_queue"
    assert signal.required_follow_up_action == "evaluate_task_failure_pattern"
    assert signal.confidence > 0.5


def test_runtime_staleness_detection_gates_on_threshold() -> None:
    signal = detect_runtime_staleness_gap(
        owner_id="owner-1",
        age_seconds=120.0,
        stale_threshold_seconds=60,
    )
    assert signal is not None
    assert signal.finding_type == "runtime_stale"
    assert signal.scope == "worker_heartbeat"


def test_contradiction_and_provenance_and_legacy_detection() -> None:
    contradiction = detect_unresolved_contradiction_gap(owner_id="owner-1", contradiction_count=5)
    provenance = detect_missing_provenance_gap(owner_id="owner-1", missing_document_count=3)
    legacy = detect_legacy_embedding_coverage_gap(owner_id="owner-1", legacy_chunk_count=11)

    assert contradiction is not None
    assert provenance is not None
    assert legacy is not None
    assert contradiction.finding_type == "unresolved_contradiction"
    assert provenance.finding_type == "missing_provenance_metadata"
    assert legacy.finding_type == "legacy_embedding_coverage"


def test_findings_deduplicate_by_owner_type_and_scope() -> None:
    now = datetime.now(UTC)
    findings = [
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="repeated_task_failures",
            scope="task_queue",
            confidence=0.3,
            evidence=(("a", 1),),
            required_follow_up_action="evaluate_task_failure_pattern",
            discovered_at=now,
        ),
        SchedulerFinding(
            owner_id="owner-1",
            finding_type="repeated_task_failures",
            scope="task_queue",
            confidence=0.8,
            evidence=(("a", 2),),
            required_follow_up_action="evaluate_task_failure_pattern",
            discovered_at=now,
        ),
        SchedulerFinding(
            owner_id="owner-2",
            finding_type="repeated_task_failures",
            scope="task_queue",
            confidence=0.5,
            evidence=(("a", 1),),
            required_follow_up_action="evaluate_task_failure_pattern",
            discovered_at=now,
        ),
    ]

    dedup = deduplicate_findings(findings=findings, max_per_owner=10)

    assert len(dedup) == 2
    assert dedup[0].owner_id == "owner-1"
    assert dedup[1].owner_id == "owner-2"
