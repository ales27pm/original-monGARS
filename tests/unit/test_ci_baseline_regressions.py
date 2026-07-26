from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import pytest

import mongars.ids as ids
import mongars.orchestrator as orchestrator
from mongars.autobiography.contracts import deep_freeze_json_mapping
from mongars.orchestrator.emotion import AffectSignal


def test_orchestrator_resolves_and_caches_lazy_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(
        orchestrator.__dict__,
        "AffectSignal",
        raising=False,
    )

    resolved = orchestrator.__getattr__("AffectSignal")

    assert resolved is AffectSignal
    assert orchestrator.__dict__["AffectSignal"] is AffectSignal
    assert "AffectSignal" in orchestrator.__dir__()

    with pytest.raises(
        AttributeError,
        match="has no attribute 'missing_export'",
    ):
        orchestrator.__getattr__("missing_export")


def test_uuid7_rejects_clock_outside_timestamp_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ids.time,
        "time_ns",
        lambda: (1 << 48) * 1_000_000,
    )

    with pytest.raises(
        RuntimeError,
        match="outside the UUIDv7 timestamp range",
    ):
        ids.uuid7()


def test_deep_frozen_json_rejects_every_mutation_surface() -> None:
    frozen = cast(
        dict[str, Any],
        deep_freeze_json_mapping({"items": [1, 2]}),
    )
    items = cast(list[Any], frozen["items"])

    mapping_mutations: tuple[Callable[[], object], ...] = (
        lambda: frozen.__setitem__("extra", 1),
        lambda: frozen.__delitem__("items"),
        frozen.clear,
        lambda: frozen.pop("items"),
        frozen.popitem,
        lambda: frozen.setdefault("extra", 1),
        lambda: frozen.update({"extra": 1}),
        lambda: frozen.__ior__({"extra": 1}),
    )

    sequence_mutations: tuple[Callable[[], object], ...] = (
        lambda: items.__setitem__(0, 3),
        lambda: items.__delitem__(0),
        lambda: items.append(3),
        items.clear,
        lambda: items.extend([3]),
        lambda: items.insert(0, 3),
        items.pop,
        lambda: items.remove(1),
        items.reverse,
        items.sort,
        lambda: items.__iadd__([3]),
        lambda: items.__imul__(2),
    )

    for mutation in (*mapping_mutations, *sequence_mutations):
        with pytest.raises(TypeError, match="immutable"):
            mutation()
