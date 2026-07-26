# Autobiographical Persistence Guidance

## Scope

This file governs `src/mongars/autobiography/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This subsystem persists owner-scoped conversation turns, generation runs,
evidence bindings, and autobiographical events used to reconstruct the factual
history of a response.

## Key Files and Interfaces

- `contracts.py`: typed, validated persistence inputs and public records.
- `service.py`: write/read orchestration and consistency checks.
- `repository.py`: owner/trace-scoped SQL operations.
- `tables.py`: subsystem table-facing definitions/helpers.
- `../orchestrator/typed_journal.py`,
  `../orchestrator/typed_evidence.py`: primary producers.
- `../db/models.py` and
  `../../../migrations/versions/0007_autobiographical_memory.py`: storage.

## Data and Control Flow

The typed journal receives a completed/failed chat outcome and its evidence,
then asks the service to persist conversation, generation, evidence, and event
records in a caller-owned transaction. Reads reconstruct records through typed
contracts rather than returning raw ORM objects.

## Local Invariants

- Owner ID, trace ID, session/turn identity, and generation identity must remain
  associated through all records.
- Evidence persisted for a generation must match the evidence/keys used by that
  generation; do not attach evidence from another owner or trace.
- Persisted payloads and statuses must pass contract validation on both write
  and reconstruction.
- Do not persist hidden reasoning, raw credentials, or unrestricted provider
  payloads.
- Append/history semantics must not be replaced with destructive overwrites.
- Journal failure semantics are controlled by the orchestrator contract; do not
  swallow integrity errors inside the repository.

## Coordinated Changes

Contract/table changes require ORM models, a new migration after `0007`,
repository conversion, typed journal/evidence producers, dialogue/API response
metadata, and tests. Retention/deletion changes require owner isolation,
generation evidence, and event-history review.

## Safe Editing Rules

Keep validation in contracts/services and SQL in the repository. Treat
persisted malformed data as an explicit data error. Preserve traceability even
when a generation fails or abstains.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_autobiography_contracts.py tests/unit/test_typed_journal_resilience.py
uv run pytest -q tests/unit/test_typed_evidence.py tests/integration/test_typed_chat_runtime.py
```

Run database migration/integration checks for storage changes.

## Common Failure Modes

- Writing a turn and generation in separate commits that can leave orphaned
  history.
- Reconstructing raw JSON without validating the stored schema.
- Dropping owner/trace filters from evidence lookup.
- Storing provider reasoning rather than the bounded answer/evidence contract.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Migration rules:
[migrations/AGENTS.md](../../../migrations/AGENTS.md).
