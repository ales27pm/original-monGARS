# ADR 0004: Signed P2P exchange envelopes and quarantine-first import

- **Status:** Proposed
- **Parent issue:** #30
- **Date:** 2026-07-23

## Context

The roadmap requires an optional, explicit peer-to-peer exchange path for knowledge artifacts. Prior work currently has no remote exchange implementation, no export/import protocol, and no quarantine pipeline. This creates a risk if remote content were added directly to active memory or embeddings without provenance controls.

This issue requires an explicit identity model and cryptographic envelope to prevent tampering, replay, and cross-owner activation.

## Decision

Introduce a cryptographically signed envelope contract before any exchange implementation:

- `protocol_version`: fixed schema version constant
- `sender_peer_id` / `recipient_peer_id`: explicit pairing identifiers
- `owner_id`: owner scope that the payload is bound to
- `issued_at` / `expires_at`: bounded replay- and freshness window
- `nonce`: single-use sender nonce
- `payload_sha256` and `payload_bytes`: deterministic envelope payload assertions
- `sender_key_id` and `signature`: HMAC signature on canonical JSON

Before import, every envelope enters a local quarantine store with bounded size and retention tracking:

- item and aggregate byte bounds;
- immutable provenance record (source peer, owner, key id, issued time);
- idempotency by envelope identifier and payload digest;
- explicit deletion and expiration workflows.

## Security requirements

- Envelope signature must be verified before any tasking path.
- Replayed nonces must be rejected.
- Expired envelopes must be rejected.
- Envelopes with revoked key IDs or invalid recipients/owners must be rejected.
- Import into active memory is only available after explicit local review and promotion from quarantine.

## Deferred implementation

This ADR covers the protocol contract and quarantine boundary. Remaining work required by issue #30 includes:

- explicit pairing UX and revocation UX,
- transport and transport-metadata minimization,
- API endpoints/tasks for import/export workflows,
- compatibility negotiation across protocol versions,
- production-readiness wiring.

## Consequences

### Positive

- cryptographic envelope integrity and ownership checks are testable and versioned before transport integration;
- import content cannot mutate active memory directly without an explicit boundary;
- provenance is attached once at staging time and retained for audit and deletion.

### Negative

- envelope validation is only as strong as the key-management policy used for pairing;
- current scope is intentionally absent from mandatory readiness checks until production wiring exists.
