"""Proposal scheduler contracts for bounded background maintenance."""

from .consolidation import (
    SchedulerProposal,
    SchedulerProposalQueueRecord,
    consolidate_findings,
    emit_scheduler_proposals,
)
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
    "SchedulerResourceBudget",
    "consolidate_findings",
    "deduplicate_findings",
    "describe_scheduler_state",
    "detect_legacy_embedding_coverage_gap",
    "detect_missing_provenance_gap",
    "detect_repeated_task_failures",
    "detect_runtime_staleness_gap",
    "detect_unresolved_contradiction_gap",
    "emit_scheduler_proposals",
    "scheduler_enabled",
    "scheduler_run_allowed",
]
