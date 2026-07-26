# Embedding Provider Guidance

## Scope

This file governs `src/mongars/embeddings/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This package defines the embedding provider contract, validates input/output
limits and embedding-space metadata, and adapts Ollama. A deterministic provider
supports controlled tests.

## Key Files and Interfaces

- `base.py`: provider protocol.
- `service.py`: batching, input checks, and embedding-space orchestration.
- `models.py`: typed vectors/provenance metadata.
- `limits.py`, `errors.py`: shared boundary behavior.
- `ollama.py`: backend HTTP adapter.
- `deterministic.py`: deterministic test implementation.
- `../memory/service.py`: primary consumer.
- `../runtime.py`, `../rm/worker.py`: readiness and worker consumers.

## Local Invariants

- Returned vector count must match input count.
- Every vector must have the configured dimensions and finite numeric values.
- Model alias and immutable digest identify the embedding space; do not infer
  compatibility from alias alone.
- Enforce per-input bytes and batch limits before provider calls.
- Preserve timeouts, bounded response handling, and typed retryability/error
  translation.
- Remote endpoint use must pass `../security/runtime_policy.py`.
- The deterministic provider must not become an implicit production fallback.
- Callers must not hold database transactions across embedding requests.

## Coordinated Changes

Provider request/response changes require Ollama tests, memory boundary tests,
runtime readiness, and Compose/mock updates. Model/digest/dimension changes
require memory provenance and reindex review plus deployment defaults. New
provider implementations must satisfy `base.py` without leaking backend payloads
to callers.

## Safe Editing Rules

Keep batching deterministic and preserve input order. Reject malformed or
dimension-mismatched responses rather than truncating/padding. Close owned HTTP
clients during process shutdown and preserve cancellation.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_embedding_service.py tests/unit/test_embedding_space.py tests/unit/test_embedding_api_errors.py
uv run pytest -q tests/unit/test_ollama_embeddings.py tests/unit/test_memory_embedding_boundary.py
```

## Common Failure Modes

- Accepting a provider response with the right count but wrong dimensions.
- Treating mutable model tags as immutable provenance.
- Reordering vectors during batching.
- Retrying non-retryable validation failures.
- Changing deployment model defaults without a stored-memory migration/reindex
  plan.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Consumer rules:
[memory/AGENTS.md](../memory/AGENTS.md).
