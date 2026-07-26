# API Boundary Guidance

## Scope

This file governs `src/mongars/api/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

The API package owns public FastAPI schemas, dependency assembly, route
behavior, HTTP error mapping, and chat-stream framing. It does not own core
domain policy or persistence.

## Key Files and Entry Points

- `dependencies.py`: request/runtime dependency composition.
- `_schemas.py`, `schemas.py`, `chat_schemas.py`: shared and route-facing
  Pydantic contracts.
- `chat_streaming.py`: NDJSON frame serialization and stream control.
- `routes/health.py`: liveness/readiness.
- `routes/chat.py`: typed chat and streamed chat.
- `routes/adaptation.py`: feedback/profile endpoints.
- `routes/tasks.py`: task creation, approval, cancellation, retry, and status.
- `routes/documents.py`, `routes/memory.py`: staging/ingestion and retrieval.
- `routes/p2p.py`, `routes/web.py`: peer exchange and static browser entry.
- `../main.py`: authoritative router registration and middleware order.

## Public Interfaces and Consumers

The mobile client in `apps/mobile/lib/api/`, static browser code in
`../web/static/`, smoke scripts, and external HTTP clients consume this package.
The `/v1/chat/stream` contract is `application/x-ndjson`; mobile parsing expects
ordered start, sources, delta, and final events.

## Outgoing Dependencies

Routes call security dependencies and domain services in `orchestrator`,
`dialogue`, `memory`, `ingestion`, `adaptation`, `rm`, and `p2p`. Repositories
are assembled through `dependencies.py`; do not instantiate a second database
session inside a route.

## Data and Control Flow

Requests pass through middleware in `../main.py`, authentication/dependency
validation, route schemas, then a domain service. Domain errors are mapped to
stable status codes and bounded response bodies. Streaming chat creates an
approved typed dialogue flow, emits NDJSON frames, and handles client
cancellation without changing the wire format.

## Local Invariants

- Register every public router explicitly in `../main.py`.
- Authenticate and preserve owner scope before reading or mutating durable data.
- Keep request and upload limits enforced before expensive parsing, inference,
  or allocation.
- Keep Pydantic request models strict where the current contract rejects extra
  or malformed fields.
- Do not emit an NDJSON `delta` before `start`, more than one terminal `final`,
  or a final answer inconsistent with accumulated deltas.
- Do not expose internal exception text, credentials, prompts, hidden reasoning,
  filesystem paths, or provider payloads.
- Health and readiness have different meanings. Readiness may depend on runtime
  components; liveness must not become an expensive dependency probe.

## Coordinated Changes

- Route/schema change: update mobile types/client contract tests, static web,
  integration tests, smoke scripts, and the applicable ADR/API document.
- New task endpoint or kind: update `../rm/contracts.py`, policy, service,
  worker, payload view, tests, and mobile task preview.
- New stream frame: update `chat_streaming.py`, `chat_schemas.py`,
  `apps/mobile/lib/api/ndjson.ts`, `apps/mobile/lib/api/client.ts`, types, and
  both Python/mobile stream tests.
- Authentication change: inspect `../security/`, middleware, Compose secrets,
  mobile credential handling, and auth tests.

## Safe Editing Rules

Add domain validation to the owning service or contract module, not only to the
route. Keep HTTP translation in routes. Reuse dependency providers so session
and client lifecycles remain testable. Preserve cancellation propagation for
streaming responses.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_api_dependencies.py tests/unit/test_auth.py tests/unit/test_health.py tests/unit/test_http_middleware.py
uv run pytest -q tests/unit/test_chat_streaming_transport.py tests/unit/test_bouche_streaming.py
uv run pytest -q tests/integration/test_api_runtime.py tests/integration/test_chat_streaming_runtime.py
```

Run `npm test` from `apps/mobile` after a public API or stream contract change.

## Common Failure Modes

- Updating a Pydantic model without updating manually maintained mobile types.
- Catching broad exceptions and turning programming errors into plausible 4xx
  responses.
- Starting inference before request/auth/size validation completes.
- Breaking stream cancellation or buffering by collecting the whole answer.
- Adding a route module but omitting its `../main.py` registration.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). There is no child guidance in
this subtree.
