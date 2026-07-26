# Cortex Orchestration Guidance

## Scope

This file governs `src/mongars/orchestrator/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

The orchestrator coordinates a user chat turn. It turns validated user input,
memory/web evidence, personality and emotion context into a typed dialogue plan,
then records typed evidence/journal outcomes around the Bouche response.

## Key Files and Entry Points

- `cortex.py`: top-level chat coordination used by API dependencies/routes.
- `typed_chat.py`: typed request/result boundary.
- `cognitive_context.py`, `_cognitive_validation.py`: bounded advisory context
  and validation.
- `typed_evidence.py`: evidence records passed to dialogue/autobiography.
- `typed_journal.py`: journal/generation persistence boundary and resilience.
- `personality.py`, `emotion.py`: bounded contextual signals.
- `../dialogue/`: Bouche execution.
- `../autobiography/`: durable turn/run/evidence records.

## Incoming and Outgoing Dependencies

The chat API is the main caller. The orchestrator calls memory retrieval,
optional web search, personality profile access, dialogue/inference, events, and
autobiographical services. Typed models, rather than route Pydantic models,
cross the internal boundary.

## Data and Control Flow

`Cortex` validates a typed chat input, resolves owner-scoped context and
evidence, constructs a bounded dialogue plan, invokes Bouche, and produces a
typed response with trace/evidence metadata. Journal persistence records
conversation and generation evidence according to its explicit failure
semantics; it must not mutate the already-approved answer content.

## Local Invariants

- Cognitive context is advisory and bounded; it must not silently override the
  current user message or security policy.
- Evidence keys are unique, stable within a turn, and retain source/owner
  association.
- Typed chat, evidence, and journal objects validate at subsystem boundaries;
  do not replace them with unstructured dictionaries.
- Preserve trace IDs across route, dialogue, event, task, and autobiography
  records.
- Optional web evidence must be identified separately from memory evidence and
  must satisfy citation requirements when the plan requires it.
- Journal failure behavior must remain explicit and tested; do not hide a core
  inference failure as a journal warning or vice versa.

## Coordinated Changes

Chat result/context changes require dialogue models/service, API schemas/stream
frames, autobiography contracts, mobile types, static web, and typed-chat tests.
Evidence changes require web search, memory result mapping, citation binding,
generation evidence persistence, and ADR review. Personality context changes
require adaptation profile contracts and mimicry safeguards.

## Safe Editing Rules

Keep orchestration policy visible in `cortex.py`; avoid embedding it in route or
provider adapters. Add fields to typed contracts with explicit validation and
all constructors updated. Do not persist provider-private prompts or hidden
reasoning in journals.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_cognitive_context.py tests/unit/test_cortex_cognitive_context.py tests/unit/test_cortex_prompt.py
uv run pytest -q tests/unit/test_typed_evidence.py tests/unit/test_typed_journal_resilience.py tests/unit/test_typed_chat_runtime.py
uv run pytest -q tests/integration/test_typed_chat_runtime.py
```

## Common Failure Modes

- Adding a typed field but missing one producer/consumer.
- Letting unbounded memory/web context exceed the dialogue context budget.
- Losing trace/evidence identity during dict conversion.
- Making journal persistence failure silently change a successful answer.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Downstream response rules:
[dialogue/AGENTS.md](../dialogue/AGENTS.md).
