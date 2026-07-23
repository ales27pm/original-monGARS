# ADR 0004: Authenticated feedback and approval-gated personality application

- **Status:** Proposed
- **Parent issue:** #24
- **Date:** 2026-07-23

## Context

ADRs 0001–0003 introduced immutable cognitive-context contracts, explicit-feedback proposals, and
owner-scoped PostgreSQL persistence. Those foundations deliberately exposed no public mutation path.
The next boundary must let the authenticated owner submit feedback and activate a direct preference
without allowing one HTTP request, the language model, or an unreviewed background process to alter
the active personality profile.

The existing task system already provides the required security properties: closed task schemas,
server-side owner identity, HMAC-bound exact payload review, expiring approval, lease ownership,
worker-side policy verification, and atomic local-mutation finalization.

## Decision

Expose three bearer-protected adaptation routes:

- `POST /v1/adaptation/feedback` records one immutable explicit feedback object.
- `GET /v1/adaptation/profile` returns the current immutable owner snapshot.
- `GET /v1/adaptation/profile/revisions` returns bounded immutable revision metadata.

Helpfulness and correction feedback are observations only. They never produce a personality
mutation. A direct `PreferenceFeedback` produces a deterministic `personality.profile.apply` task
whose complete payload is the canonical `ProfileDeltaProposal` from ADR 0002.

The task is classified as `local_mutation`. It remains in `waiting_approval` until the owner reviews
the exact task payload and submits its HMAC action digest through the existing task approval route.
The worker then:

1. revalidates the registered task schema and action classification;
2. rehydrates the exact canonical proposal from the reviewed payload;
3. obtains live task-attempt ownership;
4. asks `PersonalityRepository` to recheck persisted feedback, current revision, current digest, and
   the canonical proposal under the owner advisory lock;
5. writes the current projection, immutable revision, feedback application marker, task completion,
   and bounded autobiographical events in one database transaction.

The chat route loads the current `PersonalitySnapshot` for the authenticated owner before invoking
Cortex. Cortex continues to serialize it as advisory, untrusted wording context with its existing
prompt-budget limits. No affect classifier or implicit preference inference is introduced.

## Idempotency and concurrency

The feedback UUID is the idempotency key for the observation. Reusing it with identical canonical
content returns the existing record and proposal task. Reusing it with different content fails.
The task dedupe key is `personality.profile.apply:<feedback UUID>`.

Concurrent proposals may be reviewed, but only a proposal whose expected revision and digest still
match can apply. A stale approved proposal fails terminally rather than overwriting a newer profile.
An exact same-task repository replay is accepted only when its immutable revision matches fully.

## Privacy and event boundary

The private correction text and direct preference value remain in the owner-scoped feedback or exact
task payload. Autobiographical events record identifiers, digests, revision numbers, dimensions,
conflict status, and task outcomes—not correction text or preference values.

The active snapshot remains unable to affect authentication, owner identity, authorization, policy,
approval, retention, network egress, execution capabilities, backend selection, or safety controls.

## Deployment

The default Compose override starts `mongars.rm.adaptation_worker`, an extension of the existing
worker that preserves all current executors and adds only `personality.profile.apply`. The packaged
`mongars-worker` entry point selects the same runtime. The base worker remains reusable for focused
unit tests and narrow embedding/document test fixtures.

## Deferred decisions

This ADR does not add reset, export, deletion, rollback-to-revision, implicit feedback, classifier
inference, or web/mobile profile controls. Those remain separate review boundaries. Reset and delete
must define retention and immutable-history semantics before implementation.

## Validation required

- Strict canonical task-payload round trips and tamper rejection.
- Authentication and owner isolation for every adaptation route.
- Duplicate feedback and task-dedupe behavior.
- Exact-payload approval before profile mutation.
- Worker application, stale-proposal rejection, and atomic completion.
- Chat prompt evidence that only the current immutable snapshot is loaded.
- Tests proving private correction text and preference values do not enter autobiographical events.
- Ruff, mypy, unit, PostgreSQL integration, migration, Compose, and deployment checks.
