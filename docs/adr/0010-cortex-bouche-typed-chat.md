# ADR 0010: Cortex–Bouche typed chat integration

## Status

Accepted for implementation on `agent/cortex-bouche-autobiography-integration`.

## Context

Bouche and typed Autobiographical Memory now exist, but production chat still invokes the model and writes generic `episodic_events` directly inside Cortex. The next phase must connect those modules without holding PostgreSQL transactions across web search, embedding, or model generation and without giving Bouche persistence or tool authority.

## Decision

Add a staged `TypedChatRuntime` used by PostgreSQL-backed API sessions.

1. Cortex policy validates the request, resolves the active model, retrieves bounded history, performs approved web search, and retrieves Hippocampus evidence.
2. History remains compatible during rollout: legacy generic messages form the pre-migration prefix and typed turns form the newer suffix.
3. Prompt data receives request-scoped evidence keys: `H` for history, `M` for memory, `W` for web, and `P` for owner-reviewed advisory context.
4. The accepted user turn, generation start, canonical prompt hash, exact evidence snapshots, and retrieval/search events commit before model invocation.
5. Bouche receives one immutable `DialoguePlan` and runs with no database, tool, approval, filesystem, or endpoint-selection authority.
6. The final assistant turn, generation completion, citations, tokens, latency, and completion events commit atomically. Failure or cancellation records a terminal generation state without a final assistant turn.
7. The API remains backward compatible and adds server-validated citation metadata. Expo receives the typed contract in this phase; token streaming and cancellation transport are isolated in a later change.

## Consequences

- PostgreSQL connections are not retained across model generation.
- Retrieved evidence remains untrusted but becomes exactly auditable through immutable snapshots and stable source keys.
- Model-generated URLs are not accepted; application code renders trusted source metadata.
- Existing clients continue to consume `answer`, `sources`, and `memory_hits`, while newer clients can render typed citations.
- Generic `episodic_events` remain only as a temporary history compatibility prefix. A later backfill can remove that seam.
- Bouche's internal citation-correction retry remains one logical generation run. A future attempt-level schema may record each retry prompt independently if operational evidence requires it.
