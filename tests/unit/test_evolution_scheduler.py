from __future__ import annotations

from mongars.config import Settings
from mongars.evolution.scheduler import (
    describe_scheduler_state,
    scheduler_budgets,
    scheduler_enabled,
    scheduler_run_allowed,
)


def test_scheduler_run_is_disabled_by_default() -> None:
    settings = Settings()
    assert scheduler_enabled(settings) is False
    readiness = describe_scheduler_state(
        settings=settings,
        is_idle=False,
    )
    assert readiness.enabled is False
    assert readiness.status == "disabled"
    assert readiness.can_run is False


def test_scheduler_state_reports_block_when_idle_resource_constraints_fail() -> None:
    settings = Settings(
        evolution_scheduler_enabled=True,
        evolution_scheduler_allow_network=True,
        evolution_scheduler_cpu_percent_cap=10,
    )
    ready = describe_scheduler_state(
        settings=settings,
        is_idle=True,
        runtime_cpu_percent=50.0,
    )
    assert ready.can_run is False
    assert ready.status == "blocked"
    assert ready.reason == "resource_or_queue_gate_blocked"


def test_scheduler_can_run_within_gates() -> None:
    settings = Settings(
        evolution_scheduler_enabled=True,
        evolution_scheduler_allow_network=True,
        evolution_scheduler_cpu_percent_cap=80,
        evolution_scheduler_memory_megabytes_cap=2048,
        evolution_scheduler_proposal_count_budget=5,
        evolution_scheduler_database_row_budget=100,
        evolution_scheduler_storage_budget_bytes=10_000_000,
    )
    budgets = scheduler_budgets(settings)
    assert scheduler_run_allowed(
        settings=settings,
        is_idle=True,
        runtime_cpu_percent=10.0,
        runtime_memory_mb=200.0,
        queued_proposal_count=0,
        available_db_rows=50,
        storage_used_bytes=50,
    )
    assert budgets.database_row_budget == 100


def test_scheduler_run_is_blocked_when_network_egress_is_disabled() -> None:
    settings = Settings(evolution_scheduler_enabled=True, evolution_scheduler_allow_network=False)
    ready = describe_scheduler_state(
        settings=settings,
        is_idle=True,
        runtime_cpu_percent=5.0,
        runtime_memory_mb=100.0,
    )
    assert ready.can_run is False
    assert ready.status == "blocked"
    assert ready.reason == "network_egress_disabled"
