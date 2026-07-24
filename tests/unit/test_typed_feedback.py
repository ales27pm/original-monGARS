from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from mongars.adaptation.feedback import CorrectionFeedback, HelpfulnessFeedback
from mongars.adaptation.typed_feedback import (
    ResolvedResponseTarget,
    ResponseTraceIntegrityError,
    ResponseTraceNotFound,
    record_typed_feedback_event,
    resolve_owned_response_target,
)


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _Session:
    def __init__(
        self,
        *,
        typed_rows: list[tuple[object, ...]] | None = None,
        legacy_id: object | None = None,
    ) -> None:
        self.typed_rows = typed_rows or []
        self.legacy_id = legacy_id

    async def execute(self, _statement: object) -> _Rows:
        return _Rows(self.typed_rows)

    async def scalar(self, _statement: object) -> object | None:
        return self.legacy_id


@pytest.mark.asyncio
async def test_resolves_one_owner_scoped_completed_typed_response() -> None:
    run_id = uuid4()
    session_id = uuid4()
    turn_id = uuid4()

    resolved = await resolve_owned_response_target(
        session=_Session(  # type: ignore[arg-type]
            typed_rows=[(run_id, session_id, turn_id, "restricted", "ttl_30d")]
        ),
        owner_id="owner",
        trace_id="trc_" + ("a" * 32),
    )

    assert resolved.generation_run_id == run_id
    assert resolved.session_id == session_id
    assert resolved.assistant_turn_id == turn_id
    assert resolved.sensitivity == "restricted"
    assert resolved.retention_class == "ttl_30d"
    assert resolved.is_typed is True


@pytest.mark.asyncio
async def test_rejects_ambiguous_or_missing_response_traces() -> None:
    row = (uuid4(), uuid4(), uuid4(), "private", "keep")
    with pytest.raises(ResponseTraceIntegrityError):
        await resolve_owned_response_target(
            session=_Session(typed_rows=[row, row]),  # type: ignore[arg-type]
            owner_id="owner",
            trace_id="trc_" + ("b" * 32),
        )

    with pytest.raises(ResponseTraceNotFound):
        await resolve_owned_response_target(
            session=_Session(),  # type: ignore[arg-type]
            owner_id="owner",
            trace_id="trc_" + ("c" * 32),
        )


@pytest.mark.asyncio
async def test_legacy_response_trace_remains_accepted_without_typed_event() -> None:
    resolved = await resolve_owned_response_target(
        session=_Session(legacy_id=uuid4()),  # type: ignore[arg-type]
        owner_id="owner",
        trace_id="trc_" + ("d" * 32),
    )

    assert resolved.is_typed is False
    assert resolved.assistant_turn_id is None
    assert resolved.sensitivity is None
    assert resolved.retention_class is None


@pytest.mark.asyncio
async def test_correction_event_is_content_minimized_and_inherits_governance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    class _Autobiography:
        def __init__(self, _session: object) -> None:
            return None

        async def record_event(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "mongars.adaptation.typed_feedback.AutobiographyService",
        _Autobiography,
    )
    feedback = CorrectionFeedback(
        feedback_id=uuid4(),
        response_trace_id="trc_" + ("e" * 32),
        correction_text="Private corrected answer",
    )
    target = ResolvedResponseTarget(
        trace_id=feedback.response_trace_id,
        generation_run_id=uuid4(),
        session_id=uuid4(),
        assistant_turn_id=uuid4(),
        sensitivity="restricted",
        retention_class="ttl_30d",
    )

    await record_typed_feedback_event(
        session=_Session(),  # type: ignore[arg-type]
        owner_id="owner",
        target=target,
        feedback=feedback,
    )

    assert len(recorded) == 1
    assert recorded[0]["event_type"] == "correction_received"
    assert recorded[0]["sensitivity"] == "restricted"
    assert recorded[0]["retention_class"] == "ttl_30d"
    assert recorded[0]["payload"] == {
        "target_turn_id": target.assistant_turn_id,
        "correction_id": feedback.feedback_id,
        "character_count": len(feedback.correction_text),
    }
    assert feedback.correction_text not in repr(recorded[0])


@pytest.mark.asyncio
async def test_helpfulness_event_maps_to_typed_rating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    class _Autobiography:
        def __init__(self, _session: object) -> None:
            return None

        async def record_event(self, **kwargs: Any) -> None:
            recorded.append(kwargs)

    monkeypatch.setattr(
        "mongars.adaptation.typed_feedback.AutobiographyService",
        _Autobiography,
    )
    feedback = HelpfulnessFeedback(
        feedback_id=uuid4(),
        response_trace_id="trc_" + ("f" * 32),
        helpful=False,
    )
    target = ResolvedResponseTarget(
        trace_id=feedback.response_trace_id,
        generation_run_id=uuid4(),
        session_id=uuid4(),
        assistant_turn_id=uuid4(),
        sensitivity="private",
        retention_class="keep",
    )

    await record_typed_feedback_event(
        session=_Session(),  # type: ignore[arg-type]
        owner_id="owner",
        target=target,
        feedback=feedback,
    )

    assert recorded[0]["event_type"] == "feedback_received"
    assert recorded[0]["payload"] == {
        "target_turn_id": target.assistant_turn_id,
        "rating": "down",
        "tags": ["explicit_helpfulness"],
    }
