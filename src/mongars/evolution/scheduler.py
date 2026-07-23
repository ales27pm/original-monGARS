from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mongars.config import Settings


@dataclass(frozen=True, slots=True)
class SchedulerResourceBudget:
    """Bounded execution budgets for one scheduler pass."""

    cpu_percent: float
    memory_megabytes: int
    wall_clock_seconds: int
    database_row_budget: int
    proposal_count_budget: int
    storage_bytes: int
    proposal_cooldown_minutes: int
    allow_network: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_megabytes": self.memory_megabytes,
            "wall_clock_seconds": self.wall_clock_seconds,
            "database_row_budget": self.database_row_budget,
            "proposal_count_budget": self.proposal_count_budget,
            "storage_bytes": self.storage_bytes,
            "proposal_cooldown_minutes": self.proposal_cooldown_minutes,
            "allow_network": self.allow_network,
        }


@dataclass(frozen=True, slots=True)
class SchedulerReadiness:
    enabled: bool
    status: str
    reason: str
    can_run: bool
    budgets: SchedulerResourceBudget

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "reason": self.reason,
            "can_run": self.can_run,
            "budgets": self.budgets.to_payload(),
        }


@dataclass(frozen=True, slots=True)
class SchedulerCapabilitySummary:
    status: str
    reason: str
    can_run: bool
    budgets: SchedulerResourceBudget


def scheduler_enabled(settings: Settings) -> bool:
    return bool(settings.evolution_scheduler_enabled)


def scheduler_budgets(settings: Settings) -> SchedulerResourceBudget:
    return SchedulerResourceBudget(
        cpu_percent=float(settings.evolution_scheduler_cpu_percent_cap),
        memory_megabytes=settings.evolution_scheduler_memory_megabytes_cap,
        wall_clock_seconds=settings.evolution_scheduler_wall_clock_seconds,
        database_row_budget=settings.evolution_scheduler_database_row_budget,
        proposal_count_budget=settings.evolution_scheduler_proposal_count_budget,
        storage_bytes=settings.evolution_scheduler_storage_budget_bytes,
        proposal_cooldown_minutes=settings.evolution_scheduler_cooldown_minutes,
        allow_network=settings.evolution_scheduler_allow_network,
    )


def scheduler_run_allowed(
    *,
    settings: Settings,
    is_idle: bool,
    runtime_cpu_percent: float,
    runtime_memory_mb: float,
    queued_proposal_count: int,
    available_db_rows: int,
    storage_used_bytes: int,
) -> bool:
    if not scheduler_enabled(settings):
        return False
    if not is_idle:
        return False
    if not settings.evolution_scheduler_allow_network:
        return False
    if settings.evolution_scheduler_idle_window_seconds <= 0:
        return False
    budgets = scheduler_budgets(settings)
    if runtime_cpu_percent > budgets.cpu_percent:
        return False
    if runtime_memory_mb > budgets.memory_megabytes:
        return False
    if queued_proposal_count >= budgets.proposal_count_budget:
        return False
    if available_db_rows < 1 or available_db_rows > budgets.database_row_budget:
        return False
    if storage_used_bytes > budgets.storage_bytes:
        return False
    if budgets.wall_clock_seconds <= 0:
        return False
    return True


def describe_scheduler_state(
    *,
    settings: Settings,
    is_idle: bool,
    runtime_cpu_percent: float = 0.0,
    runtime_memory_mb: float = 0.0,
    queued_proposal_count: int = 0,
    available_db_rows: int = 1,
    storage_used_bytes: int = 0,
) -> SchedulerReadiness:
    if not scheduler_enabled(settings):
        return SchedulerReadiness(
            enabled=False,
            status="disabled",
            reason="disabled_by_default",
            can_run=False,
            budgets=scheduler_budgets(settings),
        )
    can_run = scheduler_run_allowed(
        settings=settings,
        is_idle=is_idle,
        runtime_cpu_percent=runtime_cpu_percent,
        runtime_memory_mb=runtime_memory_mb,
        queued_proposal_count=queued_proposal_count,
        available_db_rows=available_db_rows,
        storage_used_bytes=storage_used_bytes,
    )
    budgets = scheduler_budgets(settings)
    if can_run:
        reason = "idle_and_within_budgets_and_network_allowed"
    elif not settings.evolution_scheduler_allow_network:
        reason = "network_egress_disabled"
    elif (
        (runtime_cpu_percent > budgets.cpu_percent)
        or (runtime_memory_mb > budgets.memory_megabytes)
        or (queued_proposal_count >= budgets.proposal_count_budget)
        or (available_db_rows < 1 or available_db_rows > budgets.database_row_budget)
        or (storage_used_bytes > budgets.storage_bytes)
        or (budgets.wall_clock_seconds <= 0)
    ):
        reason = "resource_or_queue_gate_blocked"
    else:
        reason = "resource_or_queue_gate_blocked"
    return SchedulerReadiness(
        enabled=True,
        status="ready" if can_run else "blocked",
        reason=reason,
        can_run=can_run,
        budgets=scheduler_budgets(settings),
    )
