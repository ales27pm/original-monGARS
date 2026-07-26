# Bouche Dialogue Guidance

## Scope

This file governs `src/mongars/dialogue/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

Dialogue owns the Bouche plan and response boundary between Cortex and the
inference provider. It validates prompt budgets, included evidence, streamed
text, final output, citations, and grounding status.

## Key Files and Public Interfaces

- `models.py`: dialogue plans, evidence, citations, result/stream models, and
  grounding metadata.
- `service.py`: `Bouche`, prompt construction, inference calls, stream
  chunking/fallback, answer validation, and citation binding.
- `../orchestrator/cortex.py`: incoming caller.
- `../inference/base.py`: outgoing provider protocol.
- `../api/chat_streaming.py`: external NDJSON transport consumer.

## Data and Control Flow

Cortex supplies a plan ending in the current user message, with model alias,
context budget, prompt estimate, evidence, and response mode. Bouche calls the
inference provider, validates text, binds `[source-key]` references only to
included evidence, and returns typed grounding/citation metadata. Streamed
chunks are validated before the API maps them to NDJSON frames.

## Local Invariants

- Plans have a nonempty bounded trace ID, end with a user message, use a trimmed
  model alias, and do not exceed their approved context budget.
- Evidence keys are unique. A required web citation needs included web evidence.
- Final answers are trimmed, nonempty, bounded, and contain no hidden-reasoning
  marker.
- A cited key must exist in included evidence; unknown citations are failures,
  not silently dropped references.
- Grounding status reflects abstention, absence of evidence, valid citations, or
  partial grounding exactly as modeled.
- Streaming and nonstreaming paths must enforce equivalent final-answer
  validation.

## Coordinated Changes

Plan/result model changes require orchestrator typed contracts, API schemas and
frames, autobiography generation evidence, mobile response types, static web,
and ADRs 0010/0011. Citation syntax changes require prompt templates, parser,
mobile rendering, and grounding tests. Context-budget changes require inference
settings and Cortex evidence selection.

## Safe Editing Rules

Keep citation extraction deterministic and bounded. Do not expose chain of
thought or provider thinking fields. Do not silently fabricate citations for
uncited evidence. Preserve cancellation when iterating provider streams.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_dialogue_bouche.py tests/unit/test_bouche_streaming.py
uv run pytest -q tests/unit/test_cortex_prompt.py tests/unit/test_chat_streaming_transport.py
uv run pytest -q tests/integration/test_chat_streaming_runtime.py
```

## Common Failure Modes

- Validating only the nonstreaming response path.
- Accepting a citation because it appeared in retrieved evidence even though it
  was excluded from the final plan.
- Counting characters/tokens after provider invocation instead of enforcing the
  approved prompt budget first.
- Returning whitespace or hidden-thinking content as a successful answer.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Upstream orchestration:
[orchestrator/AGENTS.md](../orchestrator/AGENTS.md).
