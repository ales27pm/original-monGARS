# Database Migration Guidance

## Scope

This file governs `migrations/` and refines the repository-root
[AGENTS.md](../AGENTS.md). Runtime ORM rules live in
[src/mongars/db/AGENTS.md](../src/mongars/db/AGENTS.md).

## Role in the System

Alembic revisions are the ordered PostgreSQL schema history used before API and
worker startup. The chain currently runs from `versions/0001_initial.py`
through `versions/0007_autobiographical_memory.py`.

## Key Files

- `env.py`: Alembic runtime and metadata integration.
- `script.py.mako`: new revision template.
- `versions/0001_initial.py`: base tables and required `pgcrypto`/`vector`
  extensions.
- `versions/0002_runtime_consistency.py`: runtime/task/memory consistency.
- `versions/0003_document_staging.py`: staged uploads.
- `versions/0004_embedding_provenance_runtime.py`: embedding provenance and
  runtime components.
- `versions/0005_personality_profiles.py`: feedback/profile history.
- `versions/0006_model_governance.py`: candidate/benchmark/activation history.
- `versions/0007_autobiographical_memory.py`: turns, generations, evidence, and
  autobiographical events.
- `../src/mongars/db/models.py`: runtime mapping that must match Alembic head.

## Public Interface and Consumers

`compose.yaml` runs migrations before API/worker readiness. CI upgrades,
downgrades to base, upgrades again, and runs `alembic check`. Every repository
and deployed database depends on this history.

## Local Invariants

- Preserve one coherent `revision`/`down_revision` chain with no accidental
  branches.
- Existing revisions may already be deployed. If deployment state is unknown,
  add a new revision instead of rewriting history.
- `upgrade` and `downgrade` must be intentional and ordered around constraints,
  indexes, backfills, and nullability.
- Data backfills must be deterministic and bounded for the expected table size;
  state any operational lock/downtime risk.
- Owner keys, task lease/approval state, vector dimensions/extensions, and
  immutable history constraints require domain-owner review.
- ORM models and migration head must agree.
- Never run destructive migration checks against a non-disposable database.

## Coordinated Changes

Schema changes require `../src/mongars/db/models.py`, owning
repositories/converters, a new revision, integration tests, deployment
ordering, and rollback/data compatibility review. JSON shape or enum-like
string changes may need an explicit data migration even if the SQL column type
is unchanged.

## Safe Editing Rules

Use Alembic operations with explicit names and reversible ordering. Do not hide
schema mutation in application startup. Do not drop data in downgrade without
documenting that consequence. Keep extension assumptions aligned with the
PostgreSQL image and CI environment.

## Validation

Working directory: repository root, against a disposable database.

```sh
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
uv run pytest -q tests/integration/test_database_runtime.py
```

## Common Failure Modes

- Editing an old revision that production has already applied.
- Adding a non-null column before backfilling existing rows.
- Creating a model/index but omitting its migration counterpart.
- Testing upgrade only and shipping a broken downgrade or divergent metadata.
- Assuming JSON payload compatibility needs no data migration.

## Parent and Child Guidance

Parent: [repository AGENTS.md](../AGENTS.md). Runtime persistence:
[src/mongars/db/AGENTS.md](../src/mongars/db/AGENTS.md).
