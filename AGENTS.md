# monGARS Repository Guidance

## Repository Purpose

This repository builds a local-first AI assistant system with three runtime
deliverables:

- A Python 3.12 FastAPI service, packaged as `mongars`, that exposes chat,
  memory, document, task, adaptation, peer-to-peer, health, and static-web
  routes.
- A separate Python task worker and an isolated document-parser service.
- An Expo/React Native mobile application under `apps/mobile`.

The default container topology adds PostgreSQL with `pgvector`, a Caddy HTTPS
edge, optional Ollama inference, and optional SearXNG search through a restricted
egress proxy. `compose.arm64.yaml` and `compose.jetson.yaml` refine the inference
deployment for those targets.

## Instruction Scope and Precedence

This file governs the entire repository. A nested `AGENTS.md` refines these
instructions for its directory and descendants. The nearest applicable file
takes precedence for local details, while this file remains authoritative for
repository-wide rules.

Explicit user instructions override repository guidance. If documentation
conflicts with executable configuration or current implementation, verify the
claim against manifests, workflows, migrations, registrations, imports, and
tests before acting. Record unresolved contradictions instead of guessing.

Preserve unrelated working-tree changes. Do not expose values from `.env`,
`secrets/`, CI secrets, tokens, HMAC keys, or deployment credentials.

## Repository Map

| Path | Ownership and guidance |
| --- | --- |
| `.github/` | CI, supply-chain scanning, dependency update configuration. Governed by this file. |
| `apps/mobile/` | Expo application, API client, secure configuration state, and Node contract tests. See [apps/mobile/AGENTS.md](apps/mobile/AGENTS.md). |
| `deploy/` | Caddy, SearXNG, and Squid edge/search configuration. See [deploy/AGENTS.md](deploy/AGENTS.md). |
| `docs/` | ADRs and deployment notes. ADRs explain intent; current code and executable configuration decide current behavior. |
| `migrations/` | Alembic schema history for PostgreSQL and `pgvector`. See [migrations/AGENTS.md](migrations/AGENTS.md). |
| `scripts/` | Local CI, deployment checks, smoke tests, credential rotation, reset, status, and benchmarking helpers. Governed by this file and `deploy/AGENTS.md` when used for deployment. |
| `src/mongars/` | Python service, worker, parser, domain services, persistence, and static web client. See [src/mongars/AGENTS.md](src/mongars/AGENTS.md). |
| `tests/` | Python unit, integration, and real-inference suites. See [tests/AGENTS.md](tests/AGENTS.md). |
| `compose*.yaml` | Runtime topology, profiles, platform overlays, and CI mocks. See [deploy/AGENTS.md](deploy/AGENTS.md). |
| `Dockerfile` | Python application image used by API, worker, parser, and migration services. |
| `Makefile` | Verified root developer command surface. There is no `make help` target. |
| `pyproject.toml`, `uv.lock` | Python package/tool configuration and locked dependency graph. |

There is no tracked `.gitmodules` file, root JavaScript workspace manifest, or
vendored source tree. `apps/mobile` is an independent npm package rather than a
declared root workspace.

## Architecture

### Runtime topology

1. `src/mongars/main.py:create_app` builds the FastAPI application, configures
   middleware and lifespan cleanup, and registers routers from
   `src/mongars/api/routes/`.
2. API dependencies compose repositories and services around the request-scoped
   SQLAlchemy session. Chat flows through the orchestrator and dialogue layers
   to inference, memory, optional web search, and autobiographical persistence.
3. Controlled or long-running mutations are written to `task_queue`. The
   `mongars-worker` entry point claims leased tasks, revalidates policy and
   integrity, performs the effect, and records completion.
4. Document upload is staged by the API. The worker calls the isolated parser,
   then embeds and persists accepted content into memory.
5. PostgreSQL is the shared durable boundary. Alembic migrations are its schema
   history; `src/mongars/db/models.py` is the ORM mapping used at runtime.
6. The mobile app and `src/mongars/web/static/` are independent HTTP clients.
   Neither shares generated client code with the Python API, so transport
   contracts must be coordinated manually.

### Dependency direction

- HTTP routes may depend on API schemas/dependencies and domain services.
  Domain modules must not import FastAPI routes.
- Services own validation and orchestration; repositories own SQLAlchemy
  persistence. Keep HTTP response construction out of repositories.
- `src/mongars/rm/worker.py` is an intentional integration boundary that may
  call memory, ingestion, adaptation, and evolution subsystems.
- Inference and embedding modules expose provider abstractions. Callers must not
  depend on Ollama response details outside those adapters.
- Mobile and static-web clients depend on the published HTTP/NDJSON behavior,
  not Python implementation details.
- Migrations may reflect model changes, but runtime code must not import
  migration modules.

No verified circular package dependency is intentionally supported. If a change
introduces a reverse dependency across these directions, treat it as an
architecture change and review all consumers.

### Executable entry points

| Unit | Entry point | Initialization and cleanup |
| --- | --- | --- |
| API | `mongars-api` -> `src/mongars/main.py:run` | `create_app` creates runtime clients and database resources; its lifespan closes web search, embeddings, inference, and database resources. |
| Worker | `mongars-worker` -> `src/mongars/rm/adaptation_worker.py:run` | Builds database, policy, inference, embedding, parser, and worker dependencies; signal-driven shutdown closes clients and database resources. |
| Parser | `src/mongars/ingestion/server.py` via Uvicorn in `compose.yaml` | Exposes the isolated document parsing process with explicit resource and request limits. |
| Mobile | `apps/mobile/app/_layout.tsx` | Expo Router mounts providers and tab routes; `MongarsProvider` restores API origin and credential state. |
| Static web | `src/mongars/web/static/index.html` and `src/mongars/web/static/app.js` | Served by the API web route; `src/mongars/web/static/runtime-recovery.js` handles runtime recovery behavior. |

### Source-of-truth files

- Package and tool versions: `pyproject.toml`, `uv.lock`,
  `apps/mobile/package.json`, `apps/mobile/package-lock.json`.
- Runtime settings and validation: `src/mongars/config.py`, `.env.example`,
  `compose.yaml`, `apps/mobile/.env.example`.
- HTTP registration and middleware: `src/mongars/main.py`,
  `src/mongars/api/dependencies.py`, `src/mongars/api/routes/`.
- Task action schemas and policy classification:
  `src/mongars/rm/contracts.py`, `src/mongars/security/policy.py`.
- Database schema history and mapping: `migrations/versions/`,
  `src/mongars/db/models.py`.
- CI expectations: `.github/workflows/ci.yml`,
  `.github/workflows/supply-chain.yml`, `scripts/ci-local.sh`.
- Architectural rationale: `docs/adr/`; executable code remains stronger
  evidence when an ADR has drifted.

## Cross-Cutting Invariants

- Owner isolation is a data boundary. Preserve `owner_id` filters, composite
  identities, and ownership checks in routes, repositories, staged uploads,
  tasks, memory, feedback, and autobiographical records.
- Privileged task effects require canonical payload validation, current policy
  classification, unexpired approval when required, and an unchanged HMAC
  action digest. Creation-time validation alone is insufficient.
- A worker may finalize a task effect only while it owns the matching execution
  token and lease. Lease loss must prevent non-finalized persistence.
- Do not hold a database transaction open across inference, embedding, remote
  parsing, or other slow network/GPU work. Prepare externally, reacquire and
  verify ownership, then persist atomically.
- Embeddings are identified by model alias, digest, and dimensions. Changing any
  part of the embedding space requires reviewing stored provenance, query
  compatibility, and reindex behavior.
- Uploaded documents are untrusted. Preserve request, object, archive, page,
  section, timeout, memory, and concurrency limits, plus isolated parsing and
  staging ownership checks.
- Chat streaming is `application/x-ndjson`. The mobile contract expects ordered
  `start`, optional `sources`, `delta`, and `final` frames, bounded frame/input
  sizes, and final-answer consistency.
- Dialogue output must remain bounded and non-empty, must not expose hidden
  reasoning markers, and may cite only evidence keys included in the approved
  dialogue plan.
- Personality adaptation is driven by typed explicit feedback. Profile
  application is revision- and digest-checked, serialized per owner, and
  recorded in immutable revision history.
- Model activation and rollback are governed state transitions. Candidate,
  benchmark, promotion, activation, and rollback records must remain
  owner-scoped and internally consistent.
- Runtime network trust is explicit. Remote inference and remote document
  parsing remain opt-in; authentication, trusted-host, CORS, request-size, and
  secure-client-origin checks must not be bypassed for convenience.
- Shutdown paths must close owned HTTP, inference, embedding, parser, and
  database resources and cancel background heartbeat tasks.

## Change Impact Matrix

| Change | Inspect together |
| --- | --- |
| HTTP route, request, response, or error shape | `src/mongars/api/`, relevant domain service, `apps/mobile/lib/api/`, `apps/mobile/types/`, `src/mongars/web/static/`, API/mobile contract tests, and relevant ADR. |
| Chat or stream frame contract | `src/mongars/api/routes/chat.py`, `src/mongars/api/chat_schemas.py`, `src/mongars/api/chat_streaming.py`, `src/mongars/orchestrator/`, `src/mongars/dialogue/`, `src/mongars/inference/`, `apps/mobile/lib/api/client.ts`, `apps/mobile/lib/api/ndjson.ts`, static web, and streaming tests. |
| Database model or persisted field | `src/mongars/db/models.py`, repository serializers, a new file under `migrations/versions/`, integration tests, and any mobile/API schema exposing it. |
| Task kind or payload | `src/mongars/rm/contracts.py`, `src/mongars/security/policy.py`, task API schemas/routes, `src/mongars/rm/service.py`, `src/mongars/rm/worker.py`, `src/mongars/rm/payload_view.py`, tests, and any mobile task preview/types. |
| Authentication, owner, or transport rule | `src/mongars/security/`, `src/mongars/config.py`, `src/mongars/main.py`, API dependencies/routes, Compose secret wiring, mobile origin/token/provider modules, static web, and auth/policy tests. |
| Embedding model, digest, dimensions, or chunk policy | `src/mongars/config.py`, `src/mongars/embeddings/`, `src/mongars/memory/`, `src/mongars/ingestion/`, `src/mongars/rm/worker.py`, Compose defaults, migration/provenance records, benchmark script, and embedding/memory tests. |
| Document type or parser behavior | Upload schemas/routes, `src/mongars/ingestion/registry.py`, extractor, parser server/client, staging, worker, limits/config, mobile upload validation, and ingestion tests. |
| Personality feedback/profile behavior | `src/mongars/adaptation/`, adaptation routes/schemas, profile task contract and worker branch, migration `0005`, mobile feedback controls/client/types, and adaptation tests. |
| Autobiographical contract | `src/mongars/autobiography/`, orchestrator typed journal/evidence, dialogue response metadata, migration `0007`, API schemas, and autobiography/chat tests. |
| Model governance | `src/mongars/evolution/governance.py`, task contracts/policy/worker, migration `0006`, config/runtime readiness, and governance/task tests. |
| Deployment setting, service, network, or secret | `src/mongars/config.py`, `.env.example`, all applicable `compose*.yaml`, `Dockerfile`, `deploy/`, deployment scripts, CI workflow, and deployment contract/smoke checks. |
| Dependency version | Manifest plus its matching lockfile, image/build configuration, supply-chain workflow, and affected validation. |

## Development Environment

- Python must satisfy `>=3.12,<3.13`.
- `uv` is the Python package manager and lockfile owner.
- Docker with Compose is required for the full PostgreSQL, edge, parser, worker,
  and optional inference/search topology.
- Node.js and npm are required in `apps/mobile`; Expo/EAS tooling is invoked by
  package scripts.
- Start local configuration from `.env.example` and
  `apps/mobile/.env.example`. Store real values only in ignored local
  configuration or the secret files mounted by `compose.yaml`.

From the repository root, install the locked Python development and document
extras with:

```sh
make sync
```

From `apps/mobile`, install the locked mobile graph with:

```sh
npm ci
```

## Build, Test, Lint, and Validation

Run commands from the repository root unless another working directory is
stated.

### Fast local checks

```sh
make lint
make typecheck
uv run pytest -q tests/unit
make compose-check
```

`make lint` runs Ruff formatting checks and linting. `make typecheck` runs mypy
over `src`. `make compose-check` validates the default Compose merge.

### Backend and integration checks

```sh
uv run pytest -q tests/integration
make test
make check
```

`make test` runs all default pytest-discovered tests. `make check` combines
lint, type checking, tests, and Compose validation. Integration tests require
`MONGARS_TEST_DATABASE_URL` pointing to a disposable PostgreSQL database. If it
is absent, all eight tracked integration modules skip and the integration-only
command exits with pytest code 5 because no tests ran. Use the CI-local path
when the local environment does not already provide the database.

### Database validation

```sh
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
uv run alembic check
```

The downgrade/upgrade/check cycle is the migration gate used in
`.github/workflows/ci.yml`; run it only against a disposable database.

### Mobile checks

Working directory: `apps/mobile`.

```sh
npm run lint
npm run typecheck
npm test
npm audit --audit-level=high
```

### Full local and deployment checks

```sh
make ci-local
uv run bandit -q -r src
uv run pip-audit
docker build --tag mongars:ci .
bash scripts/deployment_smoke.sh
```

`make ci-local` delegates to `scripts/ci-local.sh`. The GitHub workflows also
exercise migration round trips, Docker/Compose contracts, ARM64/Jetson
overlays, deployment smoke tests, secret scanning, container scanning, and SBOM
generation.

The real Ollama test at `tests/inference/test_real_ollama.py` is an explicit
external-runtime check, not a substitute for unit/integration tests.

## Generated, Vendored, and Restricted Files

- `uv.lock` is generated by `uv`; `apps/mobile/package-lock.json` is generated
  by npm. Change either only with the corresponding manifest/dependency change.
- No tracked generated API client or vendored source directory was detected.
- Do not edit dependency caches or outputs such as `.venv/`,
  `apps/mobile/node_modules/`, `apps/mobile/.expo/`, `**/__pycache__/`,
  `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`,
  coverage output, `artifacts/`, or `test-results/`.
- Do not copy values from local `.env` files or `secrets/`. Example files define
  names and shape, not production values.
- Treat existing Alembic revisions as schema history. If deployment state is
  unknown, add a new revision rather than rewriting an older one.
- Binary app assets are tracked product inputs. Do not regenerate or replace
  them unless the visual asset change is in scope and its platform references
  are reviewed.

## Security and Sensitive Areas

Security-sensitive code includes `src/mongars/security/`, `src/mongars/config.py`,
API authentication dependencies, `src/mongars/rm/`, owner-scoped repositories,
document ingestion, deployment secret mounts, Caddy/Squid/SearXNG configuration,
and mobile token/origin handling.

Never weaken validation to make a test or local deployment pass. Preserve
constant-time digest comparison, approval expiry/consumption, policy recheck,
request-size limits, URL/origin validation, owner checks, parser isolation,
network segmentation, and least-privilege container settings. Tests and logs
must use synthetic secret values and must not print credentials.

## Git and Pull Request Guidance

No repository-specific branch naming, commit-message format, PR template, or
`CONTRIBUTING.md` was found. Do not invent one.

Keep commits and PRs scoped to the requested subsystem. Dependency updates must
include the matching lockfile; schema updates must include a migration; generated
changes must name their generator. Before handoff, report the commands actually
run, tests intentionally skipped, migration/deployment impact, compatibility
risks, security-sensitive changes, and any required coordinated rollout.

## Known Hazards

- Importing `src/mongars/main.py` creates the module-level `app`; configuration
  or client construction changes can therefore affect imports and tests.
- `src/mongars/main.py:run` binds to loopback, while Compose supplies its own
  Uvicorn command for container exposure. Do not change one assuming it controls
  both.
- Slow parser, inference, or embedding calls inside a database transaction can
  exhaust the pool and undermine lease correctness.
- A task may be valid at creation and invalid at execution because policy,
  approval, risk classification, canonical payload, ownership, or lease state
  changed. Preserve execution-time verification.
- Changing an embedding model without reindex/provenance handling can return
  plausible but invalid search results.
- An NDJSON stream that looks readable may still violate frame order, terminal
  frame, size, citation, or final-answer invariants enforced by mobile clients.
- Local insecure HTTP is accepted only for loopback development by the mobile
  transport policy. Non-loopback credentials require HTTPS.
- `docs/adr/0004-p2p-knowledge-exchange.md` and
  `docs/adr/0004-personality-feedback-runtime.md` share the `0004` number.
  Treat the filenames, not the number alone, as their identities. The numbering
  collision is unresolved.
- CI covers multiple Compose overlays. A default `docker compose config` pass
  does not prove ARM64, Jetson, inference-test, or smoke overlays are valid.
- The default Compose render succeeds but warns when
  `MONGARS_OLLAMA_IMAGE_ARM64` and `MONGARS_OLLAMA_IMAGE_JETSON` are unset.
  Validate the corresponding profile with its image variable supplied before a
  platform deployment.

## Nested AGENTS.md Index

| File | Scope and reason |
| --- | --- |
| [apps/mobile/AGENTS.md](apps/mobile/AGENTS.md) | Independent Expo toolchain and manually synchronized HTTP/NDJSON contracts. |
| [deploy/AGENTS.md](deploy/AGENTS.md) | Container, network, edge, search, secret, and platform-overlay invariants. |
| [migrations/AGENTS.md](migrations/AGENTS.md) | Linear schema history and destructive validation constraints. |
| [src/mongars/AGENTS.md](src/mongars/AGENTS.md) | Python runtime layering and subsystem map. |
| [src/mongars/adaptation/AGENTS.md](src/mongars/adaptation/AGENTS.md) | Explicit feedback, profile revisions, digests, and per-owner serialization. |
| [src/mongars/api/AGENTS.md](src/mongars/api/AGENTS.md) | Public HTTP schemas, dependency composition, and streaming transport. |
| [src/mongars/autobiography/AGENTS.md](src/mongars/autobiography/AGENTS.md) | Conversation/generation evidence persistence and typed journal contracts. |
| [src/mongars/db/AGENTS.md](src/mongars/db/AGENTS.md) | SQLAlchemy lifecycle, transaction boundaries, and ORM/schema coordination. |
| [src/mongars/dialogue/AGENTS.md](src/mongars/dialogue/AGENTS.md) | Bouche planning, output validation, evidence, and citation contracts. |
| [src/mongars/embeddings/AGENTS.md](src/mongars/embeddings/AGENTS.md) | Embedding-space identity, limits, and provider boundary. |
| [src/mongars/evolution/AGENTS.md](src/mongars/evolution/AGENTS.md) | Consolidation, gap scheduling, and governed model lifecycle. |
| [src/mongars/inference/AGENTS.md](src/mongars/inference/AGENTS.md) | Ollama request/stream adapter and provider failure semantics. |
| [src/mongars/ingestion/AGENTS.md](src/mongars/ingestion/AGENTS.md) | Untrusted document staging, isolation, parsing, and resource limits. |
| [src/mongars/memory/AGENTS.md](src/mongars/memory/AGENTS.md) | Owner-scoped memory, chunking, provenance, embedding, and search. |
| [src/mongars/orchestrator/AGENTS.md](src/mongars/orchestrator/AGENTS.md) | Cortex control flow and typed cognitive/evidence/journal boundaries. |
| [src/mongars/rm/AGENTS.md](src/mongars/rm/AGENTS.md) | Controlled task contracts, approval integrity, leasing, and atomic effects. |
| [src/mongars/security/AGENTS.md](src/mongars/security/AGENTS.md) | Authentication, action policy, and runtime network trust. |
| [tests/AGENTS.md](tests/AGENTS.md) | Test-tier ownership, environment assumptions, and focused commands. |
