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
    "EMPTY_PROFILE_DIGEST",
    "MAX_PROFILE_DELTA_BYTES",
    "CorrectionFeedback",
    "ExplicitFeedback",
    "FeedbackKind",
    "HelpfulnessFeedback",
    "PreferenceFeedback",
    "ProfileDeltaProposal",
    "personality_profile_digest",
    "propose_profile_delta",
]
