# Document Ingestion Guidance

## Scope

This file governs `src/mongars/ingestion/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This subsystem accepts untrusted document bytes, stages uploads, selects a
format extractor, parses in an isolated service, and prepares normalized text
for chunking, embedding, and owner-scoped memory persistence.

## Key Files and Entry Points

- `service.py`: ingestion coordination.
- `staging.py`: staged object creation, ownership, expiry, and cleanup.
- `server.py`: isolated parser HTTP application.
- `remote.py`: worker-side parser client and response validation.
- `runtime.py`, `isolation.py`: parser runtime and process/resource boundary.
- `concurrency.py`: global and per-owner upload admission.
- `registry.py`: authoritative MIME/format-to-extractor registration.
- `extractors/`: PDF, DOCX, HTML, Markdown, text, and structural extraction.
- `models.py`, `errors.py`, `chunking.py`: typed parser contracts and limits.
- `../api/routes/documents.py`: upload/staging caller.
- `../rm/worker.py`: `document.ingest` executor.

## Incoming and Outgoing Dependencies

The documents API stages input and creates a controlled task. The worker reads
the owned staged object, calls `remote.py`, then uses `memory` and `embeddings`
to persist accepted content. Extractors call document libraries from the
`documents` Python extra. PostgreSQL stores staging metadata; staged bytes live
in the configured data path.

## Data and Control Flow

Upload validation and concurrency admission happen before staging. Staging
records bind an object to owner, digest, size, and expiry. The worker rechecks
task lease and staged-object ownership, invokes the parser without an open
database transaction, validates bounded parser output, embeds chunks, then
reacquires ownership and atomically records memory plus task completion.

## Local Invariants

- Treat filename, MIME type, extension, archive contents, markup, and parser
  output as untrusted.
- Preserve request/upload bytes, staged object count/bytes, archive entry and
  uncompressed bytes, page, section, timeout, parser memory, and concurrency
  ceilings from `../config.py`.
- A staged object is owner-bound, digest-bound, expiring, and not a general
  filesystem path supplied by a client.
- Keep parsing out of the API and worker processes.
- Do not follow remote references or enable a remote parser unless runtime
  policy explicitly permits its base URL.
- Validate parser response schema and output size before memory allocation and
  persistence.
- Do not hold a database session while waiting for parser or embeddings.
- Cleanup/retry must not delete another owner's or another task's staged object.

## Coordinated Changes

- New format: extractor, `registry.py`, MIME/filename validation in API and
  mobile, dependencies, parser image, unit/integration tests, and limits.
- Parser request/response change: `server.py`, `remote.py`, models, health check,
  Compose command, CI mock, and ingestion tests.
- Staging schema change: `staging.py`, ORM model, new migration, worker payload,
  retention cleanup, and integration tests.
- Limit change: `../config.py`, `.env.example`, Compose defaults, API/mobile
  preflight behavior, and boundary tests.

## Safe Editing Rules

Add extraction behavior behind the registry and typed parser contract. Avoid
passing raw paths or arbitrary URLs across the parser boundary. Keep blocking
library work isolated from the async API loop. Preserve deterministic
normalization so retries and digests remain stable.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_ingestion.py tests/unit/test_parser_remote.py tests/unit/test_upload_concurrency.py
uv run pytest -q tests/integration/test_document_ingestion_runtime.py
```

Run `make compose-check` after parser service, command, health, network, or
resource-limit changes.

## Common Failure Modes

- Trusting extension/MIME agreement instead of extractor validation.
- Decompressing or parsing before enforcing archive/size limits.
- Keeping a transaction open during parser or embedding calls.
- Allowing retries to consume a staged object without ownership and digest
  checks.
- Updating backend accepted formats but not mobile upload validation.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Related memory rules:
[memory/AGENTS.md](../memory/AGENTS.md).
