# Controlled Task and Worker Guidance

## Scope

This file governs `src/mongars/rm/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

`rm` is the durable controlled-action boundary. It defines valid task kinds and
payloads, policy/approval integrity, queue state transitions, worker leasing and
heartbeats, effect execution, retries, cancellation, and result projection.

## Key Files and Entry Points

- `contracts.py`: authoritative operation registry, strict payload schemas,
  canonicalization, schema versions, and policy keys.
- `service.py`: task creation/approval and execution-time integrity verification.
- `repository.py`: transactional queue transitions, claims, leases, heartbeat,
  retry, cancel, success, and failure.
- `worker.py`: worker loop, action dispatch, lease heartbeat, slow-work
  separation, and atomic local-effect finalization.
- `adaptation_worker.py`: installed `mongars-worker` entry point.
- `runtime_heartbeat.py`: worker runtime component heartbeat.
- `payload_view.py`: safe task payload/result projection.
- `../security/policy.py`: action classification and approval requirements.
- `../api/routes/tasks.py`: external caller.

## Public Interfaces and Actions

The API and evolution scheduler create tasks; mobile and web clients inspect and
control them. `worker.py` dispatches the registered kinds, including proposal
generation/execution, sandbox echo, memory search/note/reindex, document ingest,
model candidate/benchmark/promotion/activation/rollback, and personality profile
application. The exact authoritative list and schemas are in `contracts.py`.

## Data and Control Flow

Task creation normalizes the strict payload, resolves its policy key and risk,
and persists canonical data. Approval binds owner, kind, canonical payload, and
expiry with an HMAC digest. A worker transaction claims a task with an execution
token and lease. Before execution, `TaskService.verify_for_execution` repeats
schema normalization, policy/risk, approval, expiry/consumption, and digest
checks. A heartbeat renews the lease while slow work runs. Local mutations and
task success are committed together after an execution-token ownership check.

## Local Invariants

- `contracts.py` is the only action-schema registry. Unknown kinds and extra or
  noncanonical fields are rejected.
- Add an explicit `security.policy` entry for every new kind; default behavior
  is not implicit permission.
- Risk classification stored at creation must equal current execution policy.
- Approval-required tasks need an unexpired, unmodified, correctly scoped
  digest. Approval consumption semantics must survive allowed retries.
- Only the matching execution token with a live lease may heartbeat, mutate,
  finalize, fail, or retry a running task.
- Lease loss stops non-finalized work. Do not report success after losing
  ownership.
- For local database effects, effect persistence and task success are one
  transaction via `_finalize_local_mutation`.
- Parser, inference, and embedding work happens outside transactions; ownership
  is checked again before persistence.
- Idempotency/retry paths must detect an existing effect before creating a
  duplicate.
- Action kinds listed in `_EXECUTOR_SECURITY_REVIEW_REQUIRED_KINDS` remain
  blocked until the explicit executor security-review setting permits them.
- Task payload views must not expose secret or internal-only fields.

## Coordinated Changes

A new or changed task kind requires `contracts.py`, policy, API schemas/routes,
service, worker dispatch, payload view, tests, mobile types/preview when exposed,
and migration review if persisted shape/state changes. Lease/state changes
require repository, worker heartbeat, runtime heartbeat/readiness, indexes,
integration tests, and operator timing defaults.

## Safe Editing Rules

Keep state transitions in repository methods with conditional SQL/row locks.
Keep canonical payload validation independent of API Pydantic validation.
Copy persisted payloads before executing. Never perform irreversible external
effects unless retry/idempotency and lease-loss behavior are explicitly
designed; local atomic finalization cannot roll back an external system.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_task_contracts.py tests/unit/test_task_schemas.py tests/unit/test_task_service.py
uv run pytest -q tests/unit/test_task_payload_view.py tests/unit/test_worker_runtime_heartbeat.py
uv run pytest -q tests/unit/test_personality_profile_task.py tests/unit/test_adaptation_feedback_task_verification.py
uv run pytest -q tests/integration/test_api_runtime.py tests/integration/test_document_ingestion_runtime.py
```

Use database integration and migration checks for queue schema/transition
changes.

## Common Failure Modes

- Adding a worker branch without a strict contract or policy entry.
- Trusting the payload/risk decision stored at creation without revalidation.
- Heartbeating or completing by task ID alone instead of task ID plus execution
  token.
- Holding a row lock while calling embeddings/parser/inference.
- Persisting a local effect and task success in separate transactions.
- Retrying an irreversible external effect without an idempotency key.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Security policy:
[security/AGENTS.md](../security/AGENTS.md).
