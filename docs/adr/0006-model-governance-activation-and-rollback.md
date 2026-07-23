# ADR 0006: Governed chat-model evolution workflow

- **Status:** Proposed
- **Parent issue:** #26
- **Date:** 2026-07-23
- **Related to:** task kinds `model.candidate.register`, `model.benchmark.suite.create`, `model.benchmark.run`, `model.promotion.propose`, `model.activation.apply`, `model.rollback.apply`

## Context

MonGARS must move from mutable configured chat aliases to an explicit, reviewable model governance process that can:

- resolve candidates by immutable digest,
- benchmark and persist deterministic measurements,
- compare candidates against incumbent,
- apply pointer changes atomically,
- and allow bounded rollback to a known-good artifact.

## Decision

Model-governance is split into typed, policy-controlled task kinds executed by the runtime worker:

1. Register candidate artifact identities and policy version bindings.
2. Define immutable benchmark suites with versioned policy inputs.
3. Record benchmark runs containing raw performance measures and metadata.
4. Propose promotion with explicit policy parameters and provenance hashes.
5. Apply activation and rollback by updating the active model pointer and history in one durable transaction.

No model alias is used for activation without its digest and governance policy context.

## Governance rules

1. **Digest pinning**: candidate activation depends on `(candidate_alias, candidate_digest)` matching registered rows.
2. **Immutable suite policy**: suite `id`, `version`, metrics list, thresholds, and policy versions are immutable once committed.
3. **Threshold checks**: promotion requires minimum sample size and regression controls over incumbent:
   - quality must not regress beyond configured tolerance,
   - latency/memory/failure-rate must stay within tolerance,
   - context-overlap must not regress beyond tolerance.
4. **Atomic pointer update**: activation and rollback write both model state and activation history under owned execution lock.
5. **Bounded reversible operations**: rollback is an explicit task requiring approval and writes the full new state atomically.
6. **Readiness observability**: active alias/digest/generation and rollback targets are always exposed through readiness and heartbeat contracts.

## Consequences

- Promoting a model requires explicit approval-path tasks for candidate registration, benchmarking, and change application.
- Evaluation text and model-authored rationale can be reviewed separately from the payload but cannot alone authorize activation.
- Future chat-model evolution stays scoped to chat-model governance; embedding model promotion uses a separate workflow.
