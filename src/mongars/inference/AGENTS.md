# Inference Provider Guidance

## Scope

This file governs `src/mongars/inference/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

This package defines the chat inference interface and Ollama adapter. Dialogue
and runtime readiness consume it; HTTP routes do not parse Ollama payloads
directly.

## Key Files and Interfaces

- `base.py`: inference protocol, message types, and public errors.
- `ollama.py`: bounded Ollama chat and streaming HTTP implementation.
- `../dialogue/service.py`: response validation and citation binding.
- `../runtime.py`: health/readiness consumer.
- `../config.py`: model, context, prediction, timeout, think, and remote settings.

## Data and Control Flow

Dialogue supplies a validated ordered message plan and model alias. The adapter
builds the provider request, applies timeout/response limits, translates backend
failures, and yields text chunks for streaming or a final response. Dialogue,
not the provider adapter, decides grounding and citation validity.

## Local Invariants

- Preserve message ordering and roles from the approved plan.
- Enforce configured context/output/time limits and bounded provider response
  parsing.
- Streaming must propagate cancellation and must not buffer the complete answer
  merely to simplify parsing.
- Translate known backend/protocol failures into typed inference errors while
  leaving unexpected programming errors visible.
- Remote Ollama endpoints require explicit opt-in and runtime-policy approval.
- Close the owned async HTTP client during API/worker shutdown.
- Do not expose provider hidden reasoning; dialogue also rejects hidden-thinking
  markers in final output.

## Coordinated Changes

Request payload/model options require `../config.py`, Compose defaults, Ollama
tests, CI mock, and possibly context-budget logic in dialogue. Streaming
changes require API NDJSON and mobile stream tests. Error changes require API
mapping and readiness behavior review.

## Safe Editing Rules

Contain Ollama JSON details in `ollama.py`. Keep provider interfaces small and
backend-neutral. Do not retry indefinitely or convert malformed successful
responses into empty answers.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_ollama.py tests/unit/test_ollama_chat_streaming.py
uv run pytest -q tests/unit/test_dialogue_bouche.py tests/unit/test_bouche_streaming.py
```

`uv run pytest -q tests/inference/test_real_ollama.py` requires an explicitly
configured real Ollama runtime.

## Common Failure Modes

- Provider-specific fields escaping into domain or API schemas.
- Losing cancellation during stream iteration.
- Treating a malformed/empty provider response as a valid assistant answer.
- Changing context or prediction defaults without reviewing dialogue budgets
  and Compose.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Output-contract rules:
[dialogue/AGENTS.md](../dialogue/AGENTS.md).
