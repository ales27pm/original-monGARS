# ADR 0001: Advisory cognitive context contracts

- **Status:** Proposed
- **Parent issue:** #22
- **Implementation issue:** #23
- **Date:** 2026-07-23

## Context

The Programmed Longevity roadmap requires personality, affect, and Mimétisme modules. The current Cortex already has durable conversation history and semantic retrieval, but it has no narrow contract for user-reviewed response preferences or uncertain turn-level affect.

Adding a classifier directly to Cortex would couple orchestration, inference, mutable adaptation state, and policy. It would also invite a dangerous category error: inferred emotion is uncertain contextual data, not an authority for authentication, approval, safety, retention, or action selection.

Public candidate models reinforce the need for a conservative boundary. The `j-hartmann/emotion-english-distilroberta-base` model card reports seven English labels and approximately 66% evaluation accuracy on its balanced evaluation split. Google Research's GoEmotions work reports 27 emotion labels plus neutral and an average F1 score of 0.46 for its BERT baseline, explicitly leaving substantial room for improvement.

## Decision

Introduce immutable standard-library-only contracts before selecting any classifier:

- `AffectSignal` represents one bounded observation with an explicit label, confidence, evidence count, provenance, and optional pinned model identity.
- `PersonalityPreference` represents one reviewed response-style dimension on a normalized scale.
- `PersonalitySnapshot` represents a versioned immutable set of unique reviewed preferences.
- `serialize_cognitive_context` emits deterministic, byte-bounded JSON labeled `advisory_only` and `untrusted_owner_reviewed_context`.

The contracts accept no arbitrary evidence text. They serialize only bounded metadata and reviewed values. A model-derived affect signal must include both a reviewed alias and lowercase SHA-256 artifact digest.

## Non-authority rule

Cognitive context may affect response wording only. It must never affect:

- identity or owner scoping;
- authentication or authorization;
- action classification or approval;
- model/backend URL selection;
- retention, sensitivity, or legal-hold decisions;
- network egress;
- execution capabilities;
- safety enforcement.

## Deferred decisions

This ADR does not select or deploy an emotion model, store personality data, infer preferences, or integrate mutable adaptation into Cortex. Those require separate work for explicit feedback, privacy review, persistence, prompt budgeting, evaluation datasets, and approval-gated updates.

Candidate models such as `j-hartmann/emotion-english-distilroberta-base` and `SamLowe/roberta-base-go_emotions` remain evaluation inputs only. Selection requires repository-owned benchmarks covering the user's languages and domains, calibration, abstention behavior, latency, memory, bias, and failure cases.

## Consequences

### Positive

- Cortex can later consume narrow immutable snapshots instead of owning adaptation state.
- Unknown affect is represented explicitly rather than guessed.
- Model provenance is compatible with monGARS artifact-pinning principles.
- Deterministic JSON and hard byte limits make prompt-budget integration testable.
- Policy code can reject any attempt to treat cognitive context as authority.

### Negative

- This slice does not create visible personalized behavior by itself.
- A future persistence layer must define owner scoping, version conflicts, reset/export/delete, and audit semantics.
- A future prompt integration must reserve context tokens and preserve existing behavior when context is absent.

## Validation required before acceptance

- Unit tests for all value bounds and invalid provenance combinations.
- Stable serialization tests independent of input preference order.
- Oversized-context rejection tests.
- Full strict type checking, Ruff checks, and existing unit/integration suites in repository CI.
- A separate review before any classifier dependency is introduced.

## References

- Hartmann, *Emotion English DistilRoBERTa-base* model card: https://huggingface.co/j-hartmann/emotion-english-distilroberta-base
- Demszky et al., *GoEmotions: A Dataset of Fine-Grained Emotions*: https://research.google/pubs/goemotions-a-dataset-of-fine-grained-emotions/
- Mosqueira-Rey et al., *Human-in-the-loop machine learning: a state of the art*: https://doi.org/10.1007/s10462-022-10246-w
