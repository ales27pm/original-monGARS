"""Shared deterministic validators for advisory cognitive-context contracts."""

from __future__ import annotations

import math
import re

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_unit_interval(value: object, *, field: str) -> float:
    """Return a finite float in the closed unit interval."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0.0 <= float(value) <= 1.0
    ):
        raise ValueError(f"{field} must be finite and between 0 and 1")
    return float(value)


def validate_sha256_digest(value: object, *, field: str) -> str:
    """Return one canonical lowercase SHA-256 digest."""

    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


__all__ = ["validate_sha256_digest", "validate_unit_interval"]
