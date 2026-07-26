# Test Suite Guidance

## Scope

This file governs `tests/` and refines the repository-root
[AGENTS.md](../AGENTS.md). Mobile tests are governed by
[apps/mobile/AGENTS.md](../apps/mobile/AGENTS.md).

## Role in the System

The Python suite is split into fast unit contract tests, database/runtime
integration tests, and an explicit real-Ollama inference test. Tests encode
public schemas, security decisions, source/runtime wiring, concurrency, lease,
stream, migration-facing, and provider contracts.

## Test Areas

- `unit/`: isolated services, contracts, adapters, scripts, middleware,
  orchestration, streaming, task state, and security behavior.
- `integration/`: database-backed repositories, API/runtime composition,
  ingestion, typed chat, and adaptation.
- `inference/test_real_ollama.py`: opt-in real model/runtime behavior.
- `../apps/mobile/tests/*.test.cjs`: Node tests for mobile API, credentials,
  streaming, upload, task preview, and state machines.
- `.github/workflows/ci.yml`: authoritative CI grouping and coverage gate.
- `pyproject.toml`: pytest, asyncio, and coverage configuration.

## Dependency and Fixture Boundaries

Unit tests should use deterministic/fake providers and controlled settings.
All tracked integration modules require `MONGARS_TEST_DATABASE_URL` pointing to
a disposable PostgreSQL database; without it they skip, and an
integration-only pytest invocation exits with code 5 because no tests ran.
Integration tests must keep owners, database state, and clients isolated. The
real inference test requires an explicit Ollama environment and must not
silently run as the default fake.

There is no tracked shared `conftest.py`, fixture directory, or snapshot
directory. Test helpers are local to their modules or production test adapters.

## Local Invariants

- Test the public contract and failure consequence, not only private call
  counts.
- Security/task tests must cover execution-time revalidation, wrong owner,
  stale/expired approval, changed digest/risk, and lease loss where applicable.
- Streaming tests must exercise incremental split frames, ordering, bounds,
  cancellation, and terminal consistency.
- Concurrency tests must avoid timing-only assertions when synchronization/state
  can prove the outcome.
- Integration data must be synthetic and must not use local secret values or
  personal data.
- Do not weaken production validation or export internals only to make a test
  convenient.
- Source-inspection tests for scripts/wiring do not replace runtime tests for
  the same trust boundary.

## Coordinated Changes

Choose tests by owning subsystem using its nested `AGENTS.md`. Public API changes
usually require Python route/runtime tests plus mobile contract tests.
Persistence changes require integration and migration checks. Compose/script
changes require unit contract tests plus deployment configuration/smoke checks.

## Validation Commands

Working directory: repository root.

```sh
uv run pytest -q tests/unit
uv run pytest -q tests/integration
uv run pytest -q
```

The CI coverage invocation spans `tests/unit` and `tests/integration`; use
`make ci-local` to reproduce the repository's complete local CI orchestration.

Real inference, only with an explicitly configured Ollama runtime:

```sh
uv run pytest -q tests/inference/test_real_ollama.py
```

Mobile, working directory `apps/mobile`:

```sh
npm test
```

## Common Failure Modes

- A unit fake accepts data the real adapter rejects.
- Integration tests reuse owner IDs/state and pass or fail by order.
- A stream test provides one complete frame per chunk and misses incremental
  parser defects.
- A security test asserts only an HTTP status and misses the effect/state
  mutation.
- A source-string assertion passes while runtime registration is broken.
- Real inference is assumed to be deterministic or available in default CI.

## Parent and Child Guidance

Parent: [repository AGENTS.md](../AGENTS.md). No child `AGENTS.md` is needed;
the tier-specific rules and commands are centralized here.
