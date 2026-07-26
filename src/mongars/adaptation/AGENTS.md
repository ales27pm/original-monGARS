# Adaptation and Personality Guidance

## Scope

This file governs `src/mongars/adaptation/`. It refines
[the Python runtime guidance](../AGENTS.md).

## Role in the System

Adaptation owns typed explicit feedback, deterministic feedback digests,
personality snapshots/proposals, mimicry safeguards, profile persistence, and
immutable revision history.

## Key Files and Public Interfaces

- `feedback.py`: canonical preference, helpfulness, and correction feedback.
- `typed_feedback.py`: boundary parsing/validation.
- `models.py`: personality preferences, snapshots, revisions, and proposal
  digests.
- `mimicry.py`: explicit-feedback-to-profile proposal logic.
- `repository.py`: feedback idempotency, profile reads, per-owner serialized
  application, and revision reconstruction.
- `../api/routes/adaptation.py`: external HTTP consumer.
- `../rm/contracts.py`, `../rm/worker.py`: controlled profile-apply task.
- `apps/mobile/lib/api/adaptation.ts`: independent client contract.

## Data and Control Flow

The API parses typed feedback and persists it under an owner and feedback ID.
Direct preference feedback may produce a deterministic profile delta proposal.
Applying that proposal runs as `personality.profile.apply`; the worker locks
task ownership, and the repository takes a PostgreSQL advisory transaction lock
for the owner, revalidates persisted feedback/current revision/digests, writes
the new snapshot and immutable revision, and marks the feedback application.

## Local Invariants

- Feedback IDs are idempotency keys. Reuse with different canonical content is
  an identity conflict.
- Persisted feedback digest must match its canonical payload during reads and
  profile application.
- Only direct `preference` feedback changes a profile. Helpfulness/correction
  feedback must not be reinterpreted as inferred personality preference.
- A proposal is bound to expected revision, expected profile digest, feedback
  digest, target snapshot, and proposal digest.
- Profile updates are serialized per owner and increment revision consistently.
- Applied feedback can be replayed only by the same task and must match the
  immutable revision record.
- Default profile state has an empty canonical digest.
- Mimicry must remain bounded by explicit user evidence; do not infer sensitive
  traits or silently optimize personality from model output.

## Coordinated Changes

Feedback shape changes require API schemas/routes, mobile types/client/controls,
digests, repository reconstruction, migration compatibility, task payload, and
tests. Profile dimensions or digest logic require all historical
serialization/proposal code and a stored-data migration plan. Apply-flow changes
require task policy, worker atomicity, and revision tests.

## Safe Editing Rules

Canonicalize before hashing. Keep record-to-domain conversion strict so corrupt
stored payloads fail explicitly. Preserve advisory locking and task-owned
transaction boundaries. Add new adaptation behavior through typed feedback, not
free-form model interpretation.

## Validation

Working directory: repository root.

```sh
uv run pytest -q tests/unit/test_explicit_feedback.py tests/unit/test_explicit_feedback_hardening.py tests/unit/test_typed_feedback.py
uv run pytest -q tests/unit/test_personality_profile_task.py tests/unit/test_adaptation_feedback_task_verification.py
uv run pytest -q tests/integration/test_adaptation_repository.py tests/integration/test_adaptation_api.py tests/integration/test_adaptation_api_runtime.py
```

Run `npm test` from `apps/mobile` after public feedback contract changes.

## Common Failure Modes

- Hashing an unnormalized dict/list representation.
- Treating duplicate feedback as harmless without checking its digest.
- Applying a proposal after the profile revision changed.
- Updating the current profile without an immutable revision record.
- Learning preferences from corrections/helpfulness or assistant-generated text.

## Parent and Child Guidance

Parent: [src/mongars/AGENTS.md](../AGENTS.md). Controlled execution:
[rm/AGENTS.md](../rm/AGENTS.md).
