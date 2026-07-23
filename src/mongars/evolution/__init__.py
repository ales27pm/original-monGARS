"""Proposal scheduler contracts for bounded background maintenance."""

from .gap_detection import (
    SchedulerFinding,
    SchedulerFindingType,
    deduplicate_findings,
    detect_legacy_embedding_coverage_gap,
    detect_missing_provenance_gap,
    detect_repeated_task_failures,
    detect_runtime_staleness_gap,
    detect_unresolved_contradiction_gap,
)
from .consolidation import (
    SchedulerProposal,
    SchedulerProposalQueueRecord,
    consolidate_findings,
    emit_scheduler_proposals,
)
from .scheduler import (
    SchedulerCapabilitySummary,
    SchedulerReadiness,
    SchedulerResourceBudget,
    describe_scheduler_state,
    scheduler_enabled,
    scheduler_run_allowed,
)

__all__ = [
    "SchedulerCapabilitySummary",
    "SchedulerFinding",
    "SchedulerFindingType",
    "SchedulerProposal",
    "SchedulerProposalQueueRecord",
    "SchedulerReadiness",
    "emit_scheduler_proposals",
    "SchedulerResourceBudget",
    "consolidate_findings",
    "detect_legacy_embedding_coverage_gap",
    "detect_missing_provenance_gap",
    "detect_repeated_task_failures",
    "detect_runtime_staleness_gap",
    "detect_unresolved_contradiction_gap",
    "deduplicate_findings",
    "scheduler_enabled",
    "scheduler_run_allowed",
    "describe_scheduler_state",
]
