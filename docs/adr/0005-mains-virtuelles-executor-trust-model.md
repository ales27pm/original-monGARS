# ADR 0005: Mains Virtuelles execution contract and sandbox trust model

- **Status:** Proposed
- **Parent issue:** #28
- **Date:** 2026-07-23
- **Related to:** task execution (`TaskQueue`)

## Context

The runtime currently supports a typed task queue and approval path, but it intentionally does not execute arbitrary host operations in this release. Any future executor implementation must be constrained by a formal contract before work reaches a production code path.

## Decision

Executor work is blocked on a predefined and versioned operation model implemented as a closed schema and reviewed task payloads.

- `operation_kind` values are sourced from a strict enum.
- Every operation has a static schema and policy annotations.
- No arbitrary host shell or filesystem shell-outs are permitted.
- Execution is an explicitly separate phase from approval.

Security must be enforced in both data and runtime layers.

## Security contract

1. Runtime image and process boundary

- Runtime images are versioned and immutable by digest.
- Supply-chain provenance is reviewed before trusted execution images are introduced.
- Containers run with at least: UID/GID isolation, dropped Linux capabilities, read-only root filesystem, `no-new-privileges`, seccomp/AppArmor profile, and tmpfs for volatile paths.

2. Resource and time limits

- Execution ceilings include PID, CPU, memory, file-size, and wall-clock bounds.
- Defaults favor denial: network is `none` unless an operation explicitly requires it and is allowed by policy review.

3. Capability and task model

- Operations are predefined and versioned.
- Each operation has:
  - deterministic schema,
  - explicit output and artifact size bounds,
  - immutable operation id and digest,
  - idempotency key requirement.
- The operation id and digest are bound into approval payloads.

4. Isolation and secrets

- No Docker socket, privileged container mode, writable source checkout, host credentials, or implicit secrets.
- Network paths remain explicit allowlists only.
- All executor outputs flow through quarantine and audit logging before optional downstream use.

5. Lifecycle and governance

- Approval is explicit and separate from execution.
- Execution carries exact-payload review and an idempotency key.
- Emergency disable exists and is policy-controlled.
- Lease-loss, replay, partial-failure, oversized-output, fork-bomb, disk-fill, and escape attempts are part of test plans before implementation.

## Proposed executor test plans

- Replay and approval consumption
  - Keep `test_consumed_approval_cannot_be_replayed` and `test_approval_request_cannot_be_replayed` as mandatory task-service invariants.
  - Add matrix coverage where approval replay is attempted after restart, clock drift, and concurrent worker claim races.
- Lease-loss behavior
  - Expand runtime tests around heartbeats, expired leases, and recovery transitions for each operation branch.
  - Keep event/error telemetry assertions for both requeue and terminal failure paths.
- Partial failure and rollback
  - Validate atomic boundaries around staged ingestion, memory writes, and profile updates so partial writes cannot produce an unaccounted state.
  - Include rollback assertions when downstream storage/embedding operations fail mid-flight.
- Output and resource ceiling abuse
  - Add executor-output size caps and structured truncation tests (including preview-only result payloads).
  - Reuse oversized response guards from parser and embedding surfaces as boundary patterns.
- Fork-bomb and resource abuse
  - Add process/thread caps and spawn-budget tests in the executor environment with hard stop-on-breach behavior.
  - Add CPU/memory/disk fill pressure tests that prove bounded failure and cleanup.
- Network escape and path escape
  - Test explicit deny-by-default networking with only allowlisted endpoints.
  - Test path and command-input handling to prevent filesystem traversal and socket escape even for attacker-constructed payloads.

## Implementation sequencing

- This ADR defines non-negotiable guardrails but does not authorize executor implementation yet.
- Any executor PR must obtain security review signoff against this document and include tests for failure modes above.
- Language/runtime choice (C++, Ruby, Python, WASI, or similar) remains a later decision and is explicitly out-of-scope for this issue.

## Consequences

- Positive: future executor work gains a bounded security baseline before any runtime side effects ship.
- Negative: execution capabilities are intentionally not available until all listed controls and tests are in place.
