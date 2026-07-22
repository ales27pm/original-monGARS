"""Small deterministic helpers shared by structured document extractors."""

from __future__ import annotations

from mongars.ingestion.errors import DocumentStructureLimitError
from mongars.ingestion.models import (
    MAX_HEADING_COMPONENT_CHARACTERS,
    MAX_HEADING_PATH_UTF8_BYTES,
)


class HeadingPathTracker:
    """Track a heading hierarchy without inventing missing parent levels."""

    def __init__(self) -> None:
        self._headings: dict[int, str] = {}

    @property
    def current(self) -> tuple[str, ...]:
        return tuple(value for _, value in sorted(self._headings.items()))

    def update(self, level: int, title: str) -> tuple[str, ...]:
        if level < 1:
            raise ValueError("heading level must be positive")
        normalized = " ".join(title.split())
        if not normalized:
            raise ValueError("heading title must not be empty")
        candidate = {
            existing_level: value
            for existing_level, value in self._headings.items()
            if existing_level < level
        }
        candidate[level] = normalized
        path = tuple(value for _, value in sorted(candidate.items()))
        if (
            len(normalized) > MAX_HEADING_COMPONENT_CHARACTERS
            or sum(len(value.encode("utf-8")) for value in path) > MAX_HEADING_PATH_UTF8_BYTES
        ):
            raise DocumentStructureLimitError(
                "document heading metadata exceeds the configured structural limit"
            )
        self._headings = candidate
        return self.current


def cell_reference(row_index: int, column_index: int) -> str:
    """Return an A1-style one-based display reference for zero-based indexes."""

    if row_index < 0 or column_index < 0:
        raise ValueError("table coordinates must be non-negative")
    column_number = column_index + 1
    letters = ""
    while column_number:
        column_number, remainder = divmod(column_number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return f"{letters}{row_index + 1}"


__all__ = ["HeadingPathTracker", "cell_reference"]
