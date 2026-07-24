# ADR 0009: Bouche and typed autobiographical memory core

## Status

Accepted for implementation on `agent/bouche-autobiographical-memory-core`.

## Context

Cortex currently owns retrieval, prompt packing, model invocation, response validation, and chat-event persistence. Chat history is stored as generic `episodic_events` rows whose event type and JSON payload are not schema-bound. That design was sufficient for the production kernel, but it does not provide a stable boundary for expression, exact evidence snapshots, generation lifecycle auditing, corrections, retention, or later Mimicry and Sommeil Paradoxal work.

## Decision

Introduce two bounded modules.

**Bouche** is database-free. It receives an immutable `DialoguePlan` approved by Cortex, invokes the configured inference boundary, rejects hidden-reasoning markers and unknown evidence citations, performs at most one citation-correction retry, and returns a typed `ComposedResponse`. It has no tool, approval, filesystem, network-origin, or persistence authority.

**Autobiographical Memory** stores four owner-scoped record types:

1. ordered conversation turns;
2. generation lifecycle runs;
3. immutable snapshots of the evidence actually supplied to a generation;
4. typed autobiographical events with canonical payload hashes.

The schema stores model and prompt recipe identity, token/latency metadata, grounding status, content hashes, retention, source locators, and failure state. It never stores hidden chain-of-thought. Generic task and maintenance events remain in `episodic_events` until their callers migrate to typed contracts.

## Consequences

- Cortex can be reduced to policy, context selection, and orchestration in a follow-up change.
- A failed or cancelled generation can be audited without creating a final assistant turn.
- Exact evidence survives source mutation or deletion.
- Later explicit feedback, consolidation, and evaluation can target stable turn and generation identifiers.
- The initial migration is additive and does not rewrite existing `episodic_events`; compatibility fallback remains possible during rollout.
- Streaming is intentionally deferred to a separate change so migration and transport failures stay independently reviewable.
