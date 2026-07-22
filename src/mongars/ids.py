from __future__ import annotations

import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate an RFC 9562 UUIDv7 on Python 3.12.

    The timestamp provides index locality; cryptographic random bits prevent predictable IDs.
    """

    timestamp_ms = time.time_ns() // 1_000_000
    if timestamp_ms >= 1 << 48:
        raise RuntimeError("system clock is outside the UUIDv7 timestamp range")
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (timestamp_ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return UUID(int=value)
