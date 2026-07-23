from __future__ import annotations

from uuid import uuid4

import pytest

from mongars.adaptation.feedback import CorrectionFeedback, PreferenceFeedback
from mongars.adaptation.mimicry import propose_profile_delta

_TRACE_ID = "trc_" + ("b" * 32)


def test_correction_feedback_rejects_binary_control_characters() -> None:
    with pytest.raises(ValueError, match="binary control"):
        CorrectionFeedback(
            feedback_id=uuid4(),
            response_trace_id=_TRACE_ID,
            correction_text="valid prefix\x00hidden suffix",
        )


def test_falsy_untyped_current_profile_does_not_fall_back_to_default() -> None:
    feedback = PreferenceFeedback(
        feedback_id=uuid4(),
        dimension="brevity",
        desired_value=0.5,
    )

    with pytest.raises(TypeError, match="PersonalitySnapshot"):
        propose_profile_delta({}, feedback)  # type: ignore[arg-type]
