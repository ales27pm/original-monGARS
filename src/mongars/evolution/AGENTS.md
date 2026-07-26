# Evolution and Model Governance Guidance

## Scope

This file governs `src/mongars/evolution/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This subsystem detects memory gaps, schedules retention/consolidation work, and
owns the governed lifecycle for model candidates, benchmark suites/runs,
promotion proposals, activation, and rollback.

## Key Files and Interfaces

- `consolidation.py`: memory consolidation selection/proposal behavior.
- `gap_detection.py`: knowledge-gap detection.
- `scheduler.py`: background scheduling and task creation.
- `governance.py`: candidate, benchmark, promotion, activation, rollback, and
  consistency validation.
- `../rm/contracts.py`, `../rm/worker.py`: controlled execution boundary.
- `../db/models.py`,
  `../../../migrations/versions/0006_model_governance.py`: durable state.
- `../memory/`: source memory and retention inputs.

## Data and Control Flow

The scheduler inspects owner-scoped memory/events and creates controlled tasks
rather than applying privileged effects directly. Model governance operations
run through worker actions, lock and validate owner-scoped persisted state, and
write the requested record plus task completion atomically.

## Local Invariants

- Evolution scheduling must remain owner-scoped, bounded, and idempotent enough
  to avoid duplicate work from repeated sweeps.
- Generated proposals are not authority to execute privileged actions; task
  policy and approval still apply.
- Candidate identity, immutable artifact/model digest, benchmark suite/run, and
  promotion proposal must agree before activation.
- Activation preserves prior state/history so rollback can target a verified
  previous model.
- Reject conflicts, stale proposals, cross-owner references, missing benchmark
  evidence, and malformed stored governance data.
- Memory consolidation/retention must preserve sensitivity and retention
  policy; it must not broaden data visibility.

## Coordinated Changes

Governance payload/state changes require task contracts/policy/worker, ORM
models, a new migration, settings/readiness, and tests. Scheduler timing/lease
changes require worker/runtime heartbeat and Compose defaults. Consolidation or
gap logic changes require memory contracts and evolution tests.

## Safe Editing Rules

Keep privileged state changes behind controlled task actions. Preserve
transactional conflict checks and history. Do not treat a model tag or mutable
URL as an immutable candidate identity. Keep scheduler failures observable and
bounded; do not create an unbounded task-production loop.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_evolution_consolidation.py tests/unit/test_evolution_gap_detection.py tests/unit/test_evolution_scheduler.py
uv run pytest -q tests/unit/test_task_contracts.py tests/unit/test_task_service.py
```

Run database migration/integration checks for governance persistence changes.

## Common Failure Modes

- Bypassing task approval because a proposal was generated internally.
- Activating against stale benchmark/promotion state.
- Losing previous activation history needed for rollback.
- Scheduling duplicate tasks on every retention sweep.
- Mixing model records from different owners.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Execution rules:
[rm/AGENTS.md](../rm/AGENTS.md).
