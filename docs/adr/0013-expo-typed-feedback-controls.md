# ADR 0013: Expo typed-feedback controls

## Status

Proposed on `agent/expo-typed-feedback-controls`.

## Context

The production chat path now commits typed assistant turns, exposes stable response trace identifiers, streams provisional text separately from the authoritative final response, and records explicit feedback through the owner-scoped Mimicry API. The mobile client still needs a safe way to submit helpfulness, corrections, and response-style preferences without treating transient drafts as valid feedback targets or bypassing protected profile-change approval.

## Decision

Add feedback controls only to committed assistant messages. Each submission carries a cryptographically generated UUID and the final server-issued response trace identifier.

- Helpfulness is stored as an observation and never changes the active profile.
- Corrections remain in the private explicit-feedback record and are not copied into the visible transcript, personality revisions, or autobiographical event payloads.
- Response-style preferences create `personality.profile.apply` tasks. They do not become active until the owner reviews the exact payload and approves its action digest through the existing Tasks interface.
- Failed requests retain the same feedback UUID for an exact retry, preserving backend idempotency.
- The client uses the existing origin-bound Keychain credential and HTTPS transport policy for every adaptation request.

Add a nested Personality screen under Settings that displays the current immutable snapshot, confidence and evidence counts, profile digest, and revision history. Export is an explicit user action. Reset and deletion require destructive confirmation.

## Consequences

- Transient, cancelled, or invalid streamed drafts cannot receive feedback.
- The user can inspect and reverse the active wording profile without granting model or tool authority.
- The existing protected task-review flow remains the only path for applying personality changes.
- Mobile validation must cover idempotent retries, offline and timeout behavior, owner isolation, correction-text privacy, task approval, and physical-iPhone HTTPS operation.
