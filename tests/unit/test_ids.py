from __future__ import annotations

from uuid import RFC_4122

import pytest

from mongars import ids


@pytest.mark.parametrize("random_value", [0, 1])
def test_uuid7_sets_version_and_rfc_variant_bits(
    monkeypatch: pytest.MonkeyPatch,
    random_value: int,
) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_750_000_000_123_000_000)
    monkeypatch.setattr(
        ids.secrets,
        "randbits",
        lambda bits: 0 if random_value == 0 else (1 << bits) - 1,
    )

    value = ids.uuid7()

    assert value.version == 7
    assert value.variant == RFC_4122


def test_uuid7_encodes_millisecond_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    timestamp_ms = 1_750_000_000_123
    monkeypatch.setattr(ids.time, "time_ns", lambda: timestamp_ms * 1_000_000 + 999_999)
    monkeypatch.setattr(ids.secrets, "randbits", lambda _bits: 0)

    value = ids.uuid7()

    assert value.int >> 80 == timestamp_ms


def test_uuid7_values_have_order_locality_across_milliseconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamps_ns = iter(
        [
            1_750_000_000_123_000_000,
            1_750_000_000_124_000_000,
            1_750_000_000_125_000_000,
        ]
    )
    monkeypatch.setattr(ids.time, "time_ns", lambda: next(timestamps_ns))
    monkeypatch.setattr(ids.secrets, "randbits", lambda bits: (1 << bits) - 1)

    values = [ids.uuid7(), ids.uuid7(), ids.uuid7()]

    assert [value.int for value in values] == sorted(value.int for value in values)
    assert [value.bytes for value in values] == sorted(value.bytes for value in values)
