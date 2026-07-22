"""Reviewed resource ceilings shared by semantic-processing callers."""

from __future__ import annotations

MAX_EMBEDDING_INPUTS = 4_096
MAX_EMBEDDING_TEXT_CHARACTERS = 32_000
MAX_EMBEDDING_TOTAL_CHARACTERS = 2_000_000

# UTF-8 byte limits are intentionally conservative because character or word
# counts are not safe token ceilings for code, CJK, hashes, or long identifiers.
# The per-input limit includes the reviewed purpose instruction.
MAX_EMBEDDING_TEXT_BYTES = 8_192
MAX_EMBEDDING_TOTAL_BYTES = 2_000_000
