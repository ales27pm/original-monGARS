# ADR 0003: Owner-scoped personality persistence and optimistic application

- **Status:** Proposed
- **Parent issue:** #24
- **Date:** 2026-07-23

## Context

ADR 0002 introduced immutable explicit-feedback contracts and pure profile-delta proposals. Those
values still need durable owner scoping, idempotent submission, immutable revision history, and an
atomic application boundary before authenticated APIs or worker execution can safely expose them.

A current profile row alone is insufficient. It cannot prove which reviewed feedback and task
produced a revision, cannot distinguish an idempotent retry from a conflicting replay, and cannot
protect the empty-profile case from concurrent first writes.

## Decision

Persist three separate records:

1. `explicit_feedback` stores one immutable canonical submission per `(owner_id, feedback_id)`.
2. `personality_profiles` stores only the current owner projection used to build a Cortex snapshot.
3. `personality_profile_revisions` stores every successfully applied immutable revision with its
   feedback, proposal, task, changed dimension, and conflict metadata.

The absence of a current row represents `PersonalitySnapshot.default()`. A persisted current profile
must have a positive revision, a canonical digest, and a non-empty reviewed preference array.

Feedback insertion uses PostgreSQL `ON CONFLICT DO NOTHING` followed by a canonical-content check.
The same UUID and content is an idempotent replay. Reusing the UUID with different content fails.

Profile application takes a transaction-scoped advisory lock derived from the owner ID before
reading current state. It then rechecks:

- feedback existence and canonical digest;
- that the feedback is a direct `PreferenceFeedback`;
- expected current revision and profile digest;
- canonical reconstruction of the reviewed proposal from persisted feedback;
- target profile digest and revision uniqueness.

The current projection, immutable revision, and feedback application marker are written in one
transaction. Re-execution by the same task is idempotent only when the complete immutable history
matches; a different task or changed state fails closed.

## Security and privacy boundaries

- Owner IDs are always part of primary keys and repository predicates.
- Correction text may exist in the private feedback payload, but revision and event metadata do not
  copy it.
- Persistence does not grant authority to cognitive data. Profiles remain advisory wording context.
- No API, worker task kind, automatic inference, or profile mutation is introduced by this slice.
- Later delete/reset operations must remove or tombstone data in dependency order and remain
  approval-gated where they alter the active profile.

## Consequences

### Positive

- Duplicate submissions and worker retries can be resolved deterministically.
- Optimistic revision checks prevent stale reviewed payloads from overwriting newer preferences.
- The owner advisory lock serializes concurrent first-write and update paths without a sentinel row.
- Immutable history supports export, rollback design, incident review, and future UI inspection.

### Negative

- PostgreSQL advisory locks are intentionally database-specific.
- The revision table retains feedback linkage, so privacy deletion needs an explicit transactional
  workflow rather than a blind row delete.
- The current projection is not yet loaded by the chat route until the authenticated service slice.

## Validation required

- Migration upgrade and downgrade on PostgreSQL.
- Idempotent feedback insertion and conflicting UUID rejection.
- Atomic initial application, replay, stale proposal rejection, and owner isolation.
- Strict Ruff, mypy, unit, integration, migration, and deployment checks.
