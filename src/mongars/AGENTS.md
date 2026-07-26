# Python Runtime Guidance

## Scope

This file governs `src/mongars/` except where a child `AGENTS.md` supplies more
specific rules. Read the repository-root `../../AGENTS.md` first.

## Role in the System

`mongars` contains the FastAPI process, task worker, isolated parser, all domain
services and repositories, provider clients, and the bundled static web client.
It is installed as a Python package from `pyproject.toml`.

## Key Files and Entry Points

- `main.py`: FastAPI construction, middleware, router registration, lifespan,
  module-level `app`, and `mongars-api` runner.
- `runtime.py`: shared runtime readiness/state composition.
- `config.py`: environment settings, limits, URLs, secrets, and deployment
  validation.
- `http.py`: HTTP middleware and response/security behavior.
- `logging.py`, `ids.py`, `prompting.py`, `web_search.py`: cross-cutting
  utilities with direct runtime consumers.
- `rm/adaptation_worker.py` and `rm/worker.py`: `mongars-worker` bootstrap and
  execution loop.
- `ingestion/server.py`: parser-service application.
- `web/static/`: browser client served by the API.

## Internal Structure and Dependency Direction

| Directory | Ownership | Child guidance |
| --- | --- | --- |
| `api/` | Public HTTP contracts and dependency assembly | [api/AGENTS.md](api/AGENTS.md) |
| `db/` | SQLAlchemy models, engine, sessions | [db/AGENTS.md](db/AGENTS.md) |
| `security/` | Authentication, action policy, network/runtime policy | [security/AGENTS.md](security/AGENTS.md) |
| `ingestion/` | Staging and isolated document parsing | [ingestion/AGENTS.md](ingestion/AGENTS.md) |
| `memory/` | Durable documents/chunks and retrieval | [memory/AGENTS.md](memory/AGENTS.md) |
| `embeddings/` | Embedding provider abstraction and Ollama adapter | [embeddings/AGENTS.md](embeddings/AGENTS.md) |
| `inference/` | Chat provider abstraction and Ollama adapter | [inference/AGENTS.md](inference/AGENTS.md) |
| `orchestrator/` | Cortex flow and typed context/evidence/journal contracts | [orchestrator/AGENTS.md](orchestrator/AGENTS.md) |
| `dialogue/` | Bouche prompt plan, streaming, and response validation | [dialogue/AGENTS.md](dialogue/AGENTS.md) |
| `autobiography/` | Conversation and generation evidence persistence | [autobiography/AGENTS.md](autobiography/AGENTS.md) |
| `adaptation/` | Typed explicit feedback and personality profile history | [adaptation/AGENTS.md](adaptation/AGENTS.md) |
| `rm/` | Task contracts, approvals, leases, and worker execution | [rm/AGENTS.md](rm/AGENTS.md) |
| `evolution/` | Memory evolution scheduling and model governance | [evolution/AGENTS.md](evolution/AGENTS.md) |
| `events/` | Episodic audit/event repository used by tasks and flows | This file |
| `p2p/` | Signed/validated peer protocol consumed by the P2P route | This file |
| `web/` | Static browser UI and runtime recovery | This file |

Routes depend inward on services. Services may depend on repositories and
provider protocols. Repositories depend on `db.models` and a supplied session.
Provider adapters must contain backend-specific HTTP payloads and errors.

`rm.worker` is the deliberate cross-domain executor. Do not reproduce its
integration imports in ordinary domain modules merely to avoid introducing a
service interface.

## Public Interfaces

- Console commands `mongars-api` and `mongars-worker` from `pyproject.toml`.
- FastAPI routes registered in `main.py`.
- Parser HTTP contract created in `ingestion/server.py`.
- SQL schema represented by `db/models.py` and Alembic history.
- Python dataclasses, Pydantic models, protocols, and exceptions imported by
  sibling subsystems.
- Static files under `web/static/`, consumed by browsers outside Python.

Treat exports from package `__init__.py` files and types referenced across
subsystems as public even when Python does not enforce visibility.

## Runtime Flow

`main.py:create_app` constructs or accepts runtime dependencies, adds trusted
host/CORS/request middleware, registers all routers, and closes owned clients
during lifespan shutdown. Request-scoped API dependencies create domain
services around a SQLAlchemy session. Background work crosses the durable task
queue and is handled in a separate worker process. The parser is a separate
process so untrusted extraction does not execute in the API or worker process.

## Local Invariants

- Keep application construction injectable. Tests pass controlled settings,
  database, and provider implementations to `create_app`.
- Do not perform network or GPU work while a repository transaction is open.
- Close every owned async client in the process that created it.
- Convert domain/provider failures to HTTP responses at the API boundary; do
  not make repositories depend on FastAPI exceptions.
- Keep owner identity explicit across service and repository calls.
- Use shared settings limits rather than adding route/provider-local unlimited
  paths.
- Preserve deterministic/typed test providers; they are not production fallback
  behavior unless settings explicitly select them.

## Coordinated Changes

- Adding a runtime dependency requires updating construction, injection,
  lifespan cleanup, worker/parser construction where applicable, tests, and
  deployment health/readiness.
- Adding settings requires `config.py`, `.env.example`, Compose wiring, tests,
  and mobile/deploy docs if externally configured.
- Adding a route requires `api/routes`, registration in `main.py`, schemas,
  authentication/policy review, tests, and client updates.
- Adding a persisted concept requires an ORM mapping, migration, repository,
  owner isolation, integration tests, and retention/deletion review.

## Safe Editing Rules

Keep cross-domain wiring in `main.py`, `api/dependencies.py`, or `rm/worker.py`.
Put provider-specific code in `inference/`, `embeddings/`, `ingestion/remote.py`,
or `web_search.py`. Put persistence queries in repositories. Avoid import-time
I/O: `main.py` already creates a module-level app, so additional import side
effects have broad test and deployment impact.

For `events/`, preserve append-oriented audit semantics and trace/owner
association. For `p2p/`, preserve protocol validation and never trust peer
payload ownership. For `web/static/`, coordinate API paths, authentication,
stream parsing, and recovery behavior with backend contract tests.

## Validation

Working directory: repository root.

```sh
make lint
make typecheck
uv run pytest -q tests/unit
uv run pytest -q tests/integration
```

Use the child file's focused tests first. Run `make check` for changes spanning
multiple Python subsystems.

## Common Failure Modes

- Creating clients at import time that tests cannot replace or shutdown.
- Returning raw provider exceptions through FastAPI.
- Opening nested sessions that break atomicity or leave slow work inside a
  transaction.
- Adding a setting only to Python while Compose continues supplying an
  incompatible default.
- Treating static web or mobile clients as automatically synchronized with
  Python schemas.

## Parent and Child Guidance

Parent: [repository AGENTS.md](../../AGENTS.md). Child files are listed in the
internal-structure table above. Directories without a child file remain governed
by this file.
