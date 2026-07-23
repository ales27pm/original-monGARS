# ADR 0002: Explicit feedback produces proposals, not silent adaptation

- **Status:** Proposed
- **Parent issue:** #22
- **Implementation issue:** #24
- **Date:** 2026-07-23

## Context

Mimétisme requires monGARS to learn from its owner without turning every interaction into an irreversible personality mutation. A binary helpfulness signal or a corrected answer may be valuable evidence, but neither uniquely identifies the desired response-style change. Treating those observations as direct profile instructions would create hidden learning and make contradictory behavior difficult to inspect or undo.

The existing cognitive-context boundary accepts immutable `PersonalitySnapshot` values only. Profile persistence, approval, and rollback therefore need a deterministic proposal layer between explicit feedback and any durable mutation.

## Decision

Introduce three bounded explicit-feedback contracts:

- `HelpfulnessFeedback` records a boolean assessment tied to one canonical Cortex trace.
- `CorrectionFeedback` records bounded normalized correction text tied to one canonical trace.
- `PreferenceFeedback` records one direct owner-selected personality dimension and normalized desired value, with an optional trace reference.

Every feedback value has a client-supplied UUID and deterministic SHA-256 digest. The UUID will become the idempotency identity when persistence is introduced.

Only `PreferenceFeedback` can produce a `ProfileDeltaProposal`. Helpfulness and correction feedback return no profile delta because interpreting them would require an implicit inference layer that has not passed privacy review.

A proposal contains:

- the feedback UUID and digest;
- the expected profile revision and canonical digest;
- one changed dimension;
- the previous and proposed preference values;
- an explicit conflict flag;
- the complete target preference set, target revision, and target digest.

Proposal creation is pure and has no database, network, task, or model side effects. The serialized task payload has a hard UTF-8 byte ceiling. A later persistence slice must require exact-payload approval and recheck the expected revision and digest atomically before applying it.

## Conflict behavior

A direct preference that differs from the current value is not silently merged. The proposal is marked as a conflict and includes both values for exact review. Repeating the same value increases its evidence count without creating a conflict.

## Non-authority rule

Feedback and profile proposals may influence response wording only after approval and persistence. They cannot change:

- owner identity, authentication, or authorization;
- task policy or approval requirements;
- retention, sensitivity, or legal-hold state;
- network egress or backend selection;
- execution capabilities;
- safety enforcement.

## Deferred decisions

This ADR does not add database tables, HTTP routes, task kinds, Cortex profile loading, user-interface controls, implicit feedback, or model-based interpretation. Those remain in #24 and later reviewed slices.

## Consequences

### Positive

- Ambiguous signals cannot mutate personality.
- Duplicate submissions can be content-checked through UUID plus digest.
- Contradictions are visible and reversible instead of silently overwritten.
- Future task execution can use optimistic revision and digest checks.
- The proposal payload is small enough for complete exact-payload review.

### Negative

- Helpfulness and corrections are recorded observations only until a later interpretation workflow exists.
- Visible personalization still requires persistence, approval execution, and Cortex loading.
- Direct preferences use a normalized zero-to-one scale that the UI must explain clearly.
