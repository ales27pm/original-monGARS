# ADR 0005: Approval-gated personality reset, export, and privacy deletion

- **Status:** Proposed
- **Parent issue:** #24
- **Date:** 2026-07-23

## Context

The explicit-feedback runtime can create and apply owner-reviewed preference revisions, but issue #24
also requires inspect, export, reset, and deletion controls. Reset and deletion are security-sensitive:
a direct HTTP mutation would bypass exact-payload review, while deleting only the current projection
would leave private correction text and preference values in feedback rows and historical task
payloads.

A reset also cannot return the profile to database absence. Doing so would restart the revision number
at one and collide with retained immutable preference history. The reset therefore needs a versioned,
empty reviewed snapshot rather than an unversioned row deletion.

## Decision

Expose bearer-protected lifecycle routes:

- `GET /v1/adaptation/profile/export` returns the current snapshot, preference revisions, lifecycle
  receipts, and private source feedback in a versioned JSON bundle.
- `GET /v1/adaptation/profile/lifecycle` returns privacy-safe reset and deletion receipts.
- `POST /v1/adaptation/profile/reset` creates a `personality.profile.reset` local-mutation task.
- `POST /v1/adaptation/profile/delete` creates a `personality.profile.delete` local-mutation task.

Both mutations reuse the existing HMAC-bound exact-payload approval and worker lease boundary.

A reset increments the active revision and writes an `approved_profile` snapshot with no preferences
and the canonical empty-profile digest. Existing preference revisions and source feedback remain
available for audit and export. A later direct preference continues from the incremented revision, so
history never collides or silently rewinds.

Deletion hard-deletes:

- the active profile projection;
- explicit feedback, including correction text;
- immutable preference revisions;
- prior lifecycle receipts;
- stored personality apply/reset/delete task payloads other than the executing deletion task; and
- events associated with those removed personality tasks.

The worker retains one privacy-safe deletion receipt plus the executing task result. No correction
text or preference value is copied into the receipt or event payload.

## Exact deletion-set review

The deletion task includes a `data_state_digest`. It is a SHA-256 digest over the owner-scoped profile,
feedback identifiers and digests, revision metadata, lifecycle receipts, and stored apply/reset task
payloads. The executing worker recomputes it under the owner advisory lock while briefly blocking
feedback and task-queue writes. New feedback or lifecycle state after review makes the task fail
closed instead of deleting unreviewed data.

## UI

- The bundled web control surface is available at `/personality`.
- The Expo client adds a Profile tab for inspection, native JSON sharing, and protected
  reset/deletion task creation.
- Actual mutation approval remains in the existing Tasks UI, where every canonical payload page is
  reviewable.

## Consequences

### Positive

- Reset remains versioned and does not collide with retained preference revisions.
- Export is complete enough for owner backup and incident inspection.
- Privacy deletion removes the less-obvious copies in feedback and task payload storage.
- Concurrent or stale lifecycle actions fail closed under the same owner lock as preference apply.

### Negative

- Export intentionally contains private correction text and preference values; clients must treat the
  downloaded JSON as sensitive.
- A reset is not a rollback-to-revision feature. Restoring prior values requires new explicit feedback
  or a separately reviewed future import/rollback design.
- Deletion is intentionally irreversible after worker completion.
- Downgrade to schema 0005 is blocked while lifecycle state exists because that schema cannot
  represent versioned empty reset snapshots or lifecycle receipts.

## Validation required

- Strict reset/delete task schemas and boolean rejection.
- Authenticated export, reset, lifecycle, and deletion routes.
- Reset revision continuity and empty approved snapshot loading.
- Deletion-set digest invalidation when owner data changes after review.
- Atomic removal of profile, feedback, revisions, old personality tasks, and related events.
- Web JavaScript syntax, Expo type checking, Ruff, mypy, unit tests, PostgreSQL integration, migration,
  Compose, and deployment checks.
