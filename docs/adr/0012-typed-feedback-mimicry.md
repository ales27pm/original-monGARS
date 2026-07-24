# ADR 0012: Typed response feedback and Mimicry integration

## Status

Proposed on `agent/typed-feedback-mimicry-integration`, stacked on the typed Cortex/Bouche integration.

## Context

Mimicry already accepts bounded explicit correction, helpfulness, and response-style preference feedback. Before typed chat, response ownership was verified by finding a generic Cortex `message` row in `episodic_events`.

The typed chat runtime now persists completed responses as `generation_runs` linked to final assistant `conversation_turns`. New responses are intentionally not dual-written as generic legacy messages. As a result, the existing feedback endpoint cannot recognize current Bouche responses even though they are owner-scoped and fully auditable.

Duplicating the feedback API or restoring legacy chat dual-writes would create conflicting sources of truth and duplicate conversation history.

## Decision

Resolve feedback response traces in this order:

1. one owner-scoped, completed `generation_run` with a final assistant turn;
2. the legacy Cortex message event fallback during migration;
3. otherwise reject the feedback target.

For a newly accepted feedback ID targeting a typed response, append one typed autobiographical event in the same request transaction:

- correction feedback becomes `correction_received` with target turn, feedback UUID, and character count;
- helpfulness becomes `feedback_received` with an explicit up/down rating;
- response-linked preference feedback becomes neutral `feedback_received` with bounded preference tags.

The raw correction remains only in the explicit Mimicry feedback record. It is not copied into autobiographical payloads. Events inherit the completed generation's sensitivity and retention class, and bind causation to the generation run and correlation to the feedback UUID. Idempotent duplicate submissions do not create duplicate events.

## Consequences

- Current typed Bouche responses can receive feedback without legacy dual-writes.
- Legacy responses remain reviewable during migration.
- Mimicry and Autobiographical Memory share stable turn/generation identifiers.
- Correction text is not unnecessarily replicated in the event journal.
- Feedback events cannot silently outlive a TTL-governed source response.
- Profile changes remain approval-gated through the existing personality task workflow.
