"""Pure proposal logic for owner-controlled Mimétisme profile updates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Sequence
from uuid import UUID

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.orchestrator._cognitive_validation import validate_sha256_digest
from mongars.orchestrator.personality import (
    PERSONALITY_DIMENSIONS,
    PersonalityDimension,
    PersonalityPreference,
    PersonalitySnapshot,
)

MAX_PROFILE_DELTA_BYTES = 8_192


def personality_profile_digest(preferences: Sequence[PersonalityPreference]) -> str:
    """Digest one canonical ordered preference set without owner or task metadata."""

    if any(not isinstance(preference, PersonalityPreference) for preference in preferences):
        raise TypeError("personality profile contains an invalid preference")
    ordered = sorted(preferences, key=lambda preference: preference.dimension)
    dimensions = [preference.dimension for preference in ordered]
    if len(set(dimensions)) != len(dimensions):
        raise ValueError("personality profile dimensions must be unique")
    canonical = json.dumps(
        [preference.as_dict() for preference in ordered],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


EMPTY_PROFILE_DIGEST = personality_profile_digest(())


@dataclass(frozen=True, slots=True)
class ProfileDeltaProposal:
    """One bounded, reviewable proposed profile transition.

    Creation has no side effects. Persistence and application require a separate
    approval-gated task that rechecks the expected revision and profile digest.
    """

    feedback_id: UUID
    feedback_digest: str
    expected_revision: int
    expected_profile_digest: str
    target_snapshot: PersonalitySnapshot
    changed_dimension: PersonalityDimension
    previous: PersonalityPreference | None
    proposed: PersonalityPreference
    conflict: bool

    def __post_init__(self) -> None:
        if not isinstance(self.feedback_id, UUID):
            raise TypeError("profile proposal feedback_id must be a UUID")
        validate_sha256_digest(
            self.feedback_digest,
            field="profile proposal feedback_digest",
        )
        validate_sha256_digest(
            self.expected_profile_digest,
            field="profile proposal expected_profile_digest",
        )
        if (
            isinstance(self.expected_revision, bool)
            or not isinstance(self.expected_revision, int)
            or self.expected_revision < 0
        ):
            raise ValueError("profile proposal expected_revision must be nonnegative")
        if not isinstance(self.target_snapshot, PersonalitySnapshot):
            raise TypeError("profile proposal target_snapshot must be a PersonalitySnapshot")
        if self.target_snapshot.revision != self.expected_revision + 1:
            raise ValueError("profile proposal target revision must follow the expected revision")
        if self.target_snapshot.source != "explicit_feedback":
            raise ValueError("profile proposal target must use explicit_feedback provenance")
        if self.changed_dimension not in PERSONALITY_DIMENSIONS:
            raise ValueError("profile proposal changed_dimension is unsupported")
        if not isinstance(self.proposed, PersonalityPreference):
            raise TypeError("profile proposal proposed value must be a PersonalityPreference")
        if self.proposed.dimension != self.changed_dimension:
            raise ValueError("profile proposal proposed dimension does not match its delta")
        if self.previous is not None:
            if not isinstance(self.previous, PersonalityPreference):
                raise TypeError("profile proposal previous value is invalid")
            if self.previous.dimension != self.changed_dimension:
                raise ValueError("profile proposal previous dimension does not match its delta")
        expected_conflict = (
            self.previous is not None and self.previous.value != self.proposed.value
        )
        if self.conflict is not expected_conflict:
            raise ValueError("profile proposal conflict flag does not match its values")
        target_preference = next(
            (
                preference
                for preference in self.target_snapshot.preferences
                if preference.dimension == self.changed_dimension
            ),
            None,
        )
        if target_preference != self.proposed:
            raise ValueError("profile proposal target snapshot does not contain its proposed value")
        target_digest = personality_profile_digest(self.target_snapshot.preferences)
        if self.target_snapshot.profile_digest != target_digest:
            raise ValueError("profile proposal target digest does not match its preferences")
        self.as_task_payload()

    def as_task_payload(self) -> dict[str, object]:
        """Return the exact bounded payload intended for approval review."""

        payload: dict[str, object] = {
            "changed_dimension": self.changed_dimension,
            "conflict": self.conflict,
            "expected_profile_digest": self.expected_profile_digest,
            "expected_revision": self.expected_revision,
            "feedback_digest": self.feedback_digest,
            "feedback_id": str(self.feedback_id),
            "previous": self.previous.as_dict() if self.previous is not None else None,
            "proposed": self.proposed.as_dict(),
            "target_preferences": [
                preference.as_dict() for preference in self.target_snapshot.preferences
            ],
            "target_profile_digest": self.target_snapshot.profile_digest,
            "target_revision": self.target_snapshot.revision,
        }
        canonical = _canonical_payload(payload)
        if len(canonical) > MAX_PROFILE_DELTA_BYTES:
            raise ValueError("profile delta exceeds its configured UTF-8 byte limit")
        return payload

    @property
    def proposal_digest(self) -> str:
        return hashlib.sha256(_canonical_payload(self.as_task_payload())).hexdigest()


def propose_profile_delta(
    current: PersonalitySnapshot | None,
    feedback: ExplicitFeedback,
) -> ProfileDeltaProposal | None:
    """Return a reviewable delta only for a direct, unambiguous preference statement.

    Helpfulness and correction feedback remain useful observations, but cannot safely
    infer a response-style preference without a later privacy-reviewed interpretation
    layer. They therefore never mutate the profile in this foundation.
    """

    if not isinstance(
        feedback,
        (CorrectionFeedback, HelpfulnessFeedback, PreferenceFeedback),
    ):
        raise TypeError("feedback must be an explicit feedback contract")
    if not isinstance(feedback, PreferenceFeedback):
        return None
    if current is not None and not isinstance(current, PersonalitySnapshot):
        raise TypeError("current personality must be a PersonalitySnapshot or None")

    snapshot = current if current is not None else PersonalitySnapshot.default()
    if snapshot.revision >= 2_147_483_647:
        raise ValueError("personality revision cannot be incremented")

    expected_digest = personality_profile_digest(snapshot.preferences)
    if snapshot.source != "default" and snapshot.profile_digest != expected_digest:
        raise ValueError("current personality digest does not match its preferences")

    previous = next(
        (
            preference
            for preference in snapshot.preferences
            if preference.dimension == feedback.dimension
        ),
        None,
    )
    conflict = previous is not None and previous.value != feedback.desired_value
    evidence_count = (
        min(previous.evidence_count + 1, 10_000)
        if previous is not None and not conflict
        else 1
    )
    proposed = PersonalityPreference(
        dimension=feedback.dimension,
        value=feedback.desired_value,
        confidence=1.0,
        evidence_count=evidence_count,
    )

    target_preferences = tuple(
        proposed if preference.dimension == feedback.dimension else preference
        for preference in snapshot.preferences
    )
    if previous is None:
        target_preferences = (*target_preferences, proposed)
    target_preferences = tuple(
        sorted(target_preferences, key=lambda preference: preference.dimension)
    )
    target_digest = personality_profile_digest(target_preferences)
    target_snapshot = PersonalitySnapshot(
        revision=snapshot.revision + 1,
        source="explicit_feedback",
        preferences=target_preferences,
        profile_digest=target_digest,
    )
    return ProfileDeltaProposal(
        feedback_id=feedback.feedback_id,
        feedback_digest=feedback.feedback_digest,
        expected_revision=snapshot.revision,
        expected_profile_digest=expected_digest,
        target_snapshot=target_snapshot,
        changed_dimension=feedback.dimension,
        previous=previous,
        proposed=proposed,
        conflict=conflict,
    )


def _canonical_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = [
    "EMPTY_PROFILE_DIGEST",
    "MAX_PROFILE_DELTA_BYTES",
    "ProfileDeltaProposal",
    "personality_profile_digest",
    "propose_profile_delta",
]
