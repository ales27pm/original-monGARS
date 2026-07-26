# Security Boundary Guidance

## Scope

This file governs `src/mongars/security/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This package owns bearer authentication, controlled-action policy
classification, and validation of runtime network trust. It is a shared trust
boundary for API dependencies, task creation/execution, and configuration.

## Key Files and Public Interfaces

- `auth.py`: authentication context and configured credential verification.
- `policy.py`: action/resource decisions, risk classification, and approval
  requirements.
- `runtime_policy.py`: local/remote endpoint and transport trust checks.
- `../config.py`: setting source and production-mode constraints.
- `../api/dependencies.py`: API integration.
- `../rm/service.py`, `../rm/worker.py`: creation- and execution-time policy use.

Incoming consumers rely on stable deny/allow/approval semantics. Compose secret
mounts and mobile bearer transport are coupled external boundaries.

## Security Flow

HTTP authentication establishes the configured owner context before
owner-scoped services run. Controlled tasks normalize payloads, evaluate policy,
store risk and approval metadata, and, when required, bind approval to the
canonical payload with an HMAC digest. The worker repeats policy and digest
verification immediately before execution. Runtime policy decides whether
configured inference/parser endpoints are permitted.

## Local Invariants

- Compare credentials and action digests with constant-time primitives.
- Never log credential, HMAC, token, or secret-file contents.
- Deny unknown actions. New task kinds require an explicit policy decision.
- Approval is scoped to owner, kind, canonical payload, and expiry; it is not a
  reusable global capability.
- Execution must reject changed risk classification, noncanonical payloads,
  expired/missing/consumed approvals, or digest mismatch.
- Remote inference and parser access remain disabled unless their explicit
  opt-in settings and URL policy both permit them.
- Do not weaken trusted-host, CORS, secure-origin, or loopback distinctions to
  work around local configuration.

## Coordinated Changes

- Authentication changes: `../config.py`, API dependencies/middleware, Compose
  secret wiring, mobile `apps/mobile/lib/api-token.ts`/provider/client, static
  web, and auth tests.
- Policy changes: `../rm/contracts.py`, task schemas/service/worker, tests, and
  operator/deployment expectations.
- URL/runtime trust changes: inference, embeddings, parser client, web search,
  Compose networks/proxies, examples, and runtime policy tests.

## Safe Editing Rules

Keep synthetic values in tests. Add narrow typed errors rather than broad
authentication catches. Do not expose whether a particular secret fragment was
correct. Treat owner identity changes as data migrations and authorization
changes, not display-name changes.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_auth.py tests/unit/test_policy.py tests/unit/test_config.py
uv run pytest -q tests/unit/test_task_contracts.py tests/unit/test_task_service.py
uv run bandit -q -r src
```

Run API integration and mobile credential contract tests for transport changes.

## Common Failure Modes

- Validating policy only when a task is created.
- Hashing a noncanonical payload and allowing semantically different retries.
- Treating any private-address URL as equivalent to trusted loopback.
- Returning different detailed failures that leak credential or authorization
  state.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Task-specific rules:
[rm/AGENTS.md](../rm/AGENTS.md).
