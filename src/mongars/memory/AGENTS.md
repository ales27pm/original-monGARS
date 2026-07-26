# Memory Subsystem Guidance

## Scope

This file governs `src/mongars/memory/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

Memory owns owner-scoped source documents, chunks, embedding provenance,
idempotent ingest behavior, and retrieval used by chat, tasks, document
ingestion, and evolution jobs.

## Key Files and Interfaces

- `service.py`: prepare/embed/persist/search orchestration and embedding-space
  checks.
- `repository.py`: document/chunk/provenance persistence and retrieval query.
- `chunking.py`: memory chunk boundaries and overlap behavior.
- `../embeddings/`: provider boundary.
- `../db/models.py`: persisted mappings.
- `../api/routes/memory.py`: direct HTTP consumer.
- `../rm/worker.py`: note, search, reindex, and document-ingest consumers.
- `../../../tests/unit/test_memory_*.py` and integration ingestion/database
  tests:
  contract evidence.

## Data and Control Flow

Ingest preparation validates and normalizes owner, text, metadata, sensitivity,
retention, and chunks. Embedding is performed without a repository transaction.
Persistence then resolves idempotency/provenance and writes documents/chunks in
an owned transaction. Search embeds the normalized query, then the repository
uses compatible stored embeddings and text metadata to produce bounded hits.

## Local Invariants

- Every document, chunk, provenance record, and query is owner-scoped.
- The embedding space is identified by alias, immutable digest, and dimensions.
  Do not compare or store vectors from incompatible spaces.
- Ingest retries must resolve to the same existing document when the canonical
  source identity/provenance matches.
- Chunk text and overlap must stay within configured limits and must be stable
  enough for provenance and retry behavior.
- Sensitivity and retention metadata are persisted policy inputs, not optional
  UI decoration.
- Do not call embedding providers with a transaction/session held.
- Reindex must preserve document identity and only replace embeddings under
  worker lease/ownership checks.
- Search result limits, locators, source URI/title, and score fields are public
  API/task contracts.

## Incoming and Outgoing Dependencies

Incoming callers are API memory/chat flows, Cortex, task worker, ingestion, and
evolution consolidation/gap detection. Outgoing dependencies are embeddings,
SQLAlchemy repositories/models, settings, and chunking. Memory must not import
API route types.

## Coordinated Changes

- Embedding identity/dimensions: embeddings adapter/service, settings, Compose,
  provenance schema, reindex task, tests, and stored-data rollout.
- Chunking: ingestion chunking, limits, provenance/idempotency expectations,
  benchmark script, and retrieval tests.
- Result shape: API schemas, task results, dialogue evidence, mobile types and
  client tests.
- Retention/sensitivity: evolution scheduler, deletion/consolidation behavior,
  API/task contracts, and persistence.

## Safe Editing Rules

Separate preparation/provider calls from persistence. Keep repository methods
explicit about owner and embedding space. Do not silently skip incompatible
vectors or coerce dimension mismatches into plausible results; raise a typed
boundary error.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_chunking.py tests/unit/test_memory_query.py tests/unit/test_memory_embedding_boundary.py tests/unit/test_memory_governance.py
uv run pytest -q tests/unit/test_embedding_space.py tests/integration/test_document_ingestion_runtime.py
```

Use `uv run python scripts/benchmark_memory_search.py` only as an explicit
benchmark against a configured environment, not as a unit-test replacement.

## Common Failure Modes

- Mixing vectors from different model digests or dimensions.
- Moving embedding calls into a transaction.
- Dropping owner filters from a deduplication/search shortcut.
- Changing chunk boundaries and assuming stored vectors update automatically.
- Returning text from a different owner because an ID appears globally unique.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Provider rules:
[embeddings/AGENTS.md](../embeddings/AGENTS.md).
