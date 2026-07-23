"""Owner-controlled adaptation contracts and persistence for monGARS Mimétisme."""

from mongars.adaptation.feedback import (
    CorrectionFeedback,
    ExplicitFeedback,
    FeedbackKind,
    HelpfulnessFeedback,
    PreferenceFeedback,
)
from mongars.adaptation.mimicry import (
    EMPTY_PROFILE_DIGEST,
    MAX_PROFILE_DELTA_BYTES,
    ProfileDeltaProposal,
    personality_profile_digest,
    propose_profile_delta,
)
from mongars.adaptation.repository import (
    FeedbackIdentityConflict,
    FeedbackReceipt,
    PersonalityProfileConflict,
    PersonalityProfileDataError,
    PersonalityRepository,
    PersonalityRevision,
    ProfileApplication,
)

__all__ = [
    "EMPTY_PROFILE_DIGEST",
    "MAX_PROFILE_DELTA_BYTES",
    "CorrectionFeedback",
    "ExplicitFeedback",
    "FeedbackIdentityConflict",
    "FeedbackKind",
    "FeedbackReceipt",
    "HelpfulnessFeedback",
    "PersonalityProfileConflict",
    "PersonalityProfileDataError",
    "PersonalityRepository",
    "PersonalityRevision",
    "PreferenceFeedback",
    "ProfileApplication",
    "ProfileDeltaProposal",
    "personality_profile_digest",
    "propose_profile_delta",
]
