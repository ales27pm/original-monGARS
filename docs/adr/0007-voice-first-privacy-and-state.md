# ADR 0007: Voice-first interaction privacy and fallback boundaries

- **Status:** Proposed
- **Parent issue:** #22
- **Related to:** issue #27 and privacy policy controls

## Context

Voice interaction is now first-class in the product roadmap, but it must stay bounded by privacy and governance rules already used across monGARS: owner consent, explicit reviewable mutations, bounded capture, and deterministic task controls.

## Decision

A dedicated voice privacy contract is required before broader continuous-loop expansion:

- Microphone permission is requested per session and can be revoked.
- No raw audio frames are written to persistent storage by default.
- Captured audio is scoped to a session and discarded unless a task path explicitly persists a derived text artifact after user action.
- Push-to-talk is the default mode; background/continuous capture remains opt-in and disabled by default.
- All transcription/transformation results pass through existing task/payload governance when they cross into durable mutation.
- Failure states (permission denied, network loss, timeouts, TTS interruption) are surfaced in the client and require explicit user action to continue.

## Security constraints

- Do not silently enable public search from voice mode.
- Do not mutate memory or invoke privileged execution from voice input without explicit, reviewed approval.
- Voice artifacts used for debugging must be redacted/stripped of user PII where feasible and never printed in plaintext logs.
- Voice state transitions must remain accessible to assistive technologies.

## Consequences

- Positive: voice interactions become privacy-explicit instead of behavior-only UI changes.
- Negative: no continuous or auto-listening modes are enabled in this phase.
