"""Owner-controlled adaptation contracts for monGARS Mimétisme."""

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

__all__ = [
    "CorrectionFeedback",
    "EMPTY_PROFILE_DIGEST",
    "ExplicitFeedback",
    "FeedbackKind",
    "HelpfulnessFeedback",
    "MAX_PROFILE_DELTA_BYTES",
    "PreferenceFeedback",
    "ProfileDeltaProposal",
    "personality_profile_digest",
    "propose_profile_delta",
]
