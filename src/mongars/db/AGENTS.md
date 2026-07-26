# Database Runtime Guidance

## Scope

This file governs `src/mongars/db/`. It refines
[the Python runtime guidance](../AGENTS.md). Schema-history rules live in
[migrations/AGENTS.md](../../../migrations/AGENTS.md).

## Role in the System

This package owns SQLAlchemy ORM mappings and database engine/session lifecycle.
Domain repositories elsewhere own queries and record conversion.

## Key Files and Interfaces

- `models.py`: runtime mappings for memory, tasks, events, staging,
  personality, model governance, and autobiographical records.
- `session.py`: engine/session factory creation, health checks, and cleanup.
- `__init__.py`: package exports.
- `../../../alembic.ini`, `../../../migrations/env.py`: migration integration.

Every repository in `memory`, `events`, `rm`, `adaptation`, `autobiography`, and
`evolution` is an incoming consumer. PostgreSQL plus `pgcrypto` and `vector`
extensions are the external store.

## Data and Transaction Flow

The API and worker create sessions from the shared factory and pass them to
repositories. Transaction ownership stays with the outer request/worker flow.
Repositories flush as needed but must not silently commit a caller-owned
transaction. Engine cleanup occurs during process shutdown.

## Local Invariants

- ORM columns, constraints, indexes, JSON shapes, and relationships must match
  Alembic head.
- Owner-scoped records must retain owner keys and indexes used by repository
  filters.
- Task execution token, lease, status, approval, and result fields are a single
  state-machine record; schema changes must preserve atomic conditional updates.
- Vector dimensions and embedding provenance must remain compatible with
  runtime settings and migration definitions.
- Do not hold sessions across slow parser, inference, embedding, or arbitrary
  network calls.
- Do not create independent engines/session factories inside repositories.
- Close the engine from the process lifespan/bootstrap that created it.

## Coordinated Changes

Any `models.py` change requires a new Alembic revision, repository conversion
review, integration tests, and downgrade impact review. Renaming an enum-like
string or JSON key also requires examining historical rows and worker retry
compatibility. Connection/pool changes require `../config.py`, Compose,
readiness, and runtime tests.

## Safe Editing Rules

Put query logic in the owning repository. Keep model defaults and database
defaults intentionally aligned. Do not rely on ORM validation as the sole
boundary for untrusted payloads. Avoid cascades or broad deletes until owner,
retention, and audit-history consequences are traced.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/integration/test_database_runtime.py tests/integration/test_adaptation_repository.py
uv run alembic upgrade head
uv run alembic check
```

Use the disposable migration round trip from the root guidance for schema
changes.

## Common Failure Modes

- Updating an ORM model without a migration, or a migration without the model.
- Committing inside a repository and preventing a worker effect/task
  finalization from being atomic.
- Dropping owner filters because a UUID appears globally unique.
- Changing JSON serialization while queued tasks or persisted history still use
  the previous shape.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Related schema guidance:
[migrations/AGENTS.md](../../../migrations/AGENTS.md).
