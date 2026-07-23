"""Bounded serialization of advisory cognitive context for model prompts."""

from __future__ import annotations

import json

from mongars.orchestrator.emotion import AffectSignal
from mongars.orchestrator.personality import PersonalitySnapshot

MAX_COGNITIVE_CONTEXT_BYTES = 4_096


def serialize_cognitive_context(
    *,
    personality: PersonalitySnapshot | None = None,
    affect: AffectSignal | None = None,
    maximum_bytes: int = MAX_COGNITIVE_CONTEXT_BYTES,
) -> str | None:
    """Serialize advisory context deterministically without accepting arbitrary text."""

    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int):
        raise ValueError("cognitive context maximum_bytes must be an integer")
    if not 1 <= maximum_bytes <= MAX_COGNITIVE_CONTEXT_BYTES:
        raise ValueError(
            f"cognitive context maximum_bytes must be between 1 and {MAX_COGNITIVE_CONTEXT_BYTES}"
        )
    if personality is None and affect is None:
        return None
    if personality is not None and not isinstance(personality, PersonalitySnapshot):
        raise TypeError("personality must be a PersonalitySnapshot")
    if affect is not None and not isinstance(affect, AffectSignal):
        raise TypeError("affect must be an AffectSignal")

    payload: dict[str, object] = {
        "advisory_only": True,
        "kind": "cognitive_context",
        "trust": "untrusted_owner_reviewed_context",
    }
    if affect is not None:
        payload["affect"] = affect.as_dict()
    if personality is not None:
        payload["personality"] = personality.as_dict()

    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(serialized.encode("utf-8")) > maximum_bytes:
        raise ValueError("cognitive context exceeds its configured byte limit")
    return serialized


__all__ = ["MAX_COGNITIVE_CONTEXT_BYTES", "serialize_cognitive_context"]
