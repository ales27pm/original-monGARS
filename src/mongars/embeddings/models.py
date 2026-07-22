"""Typed values exchanged across the semantic-processing boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """A validated embedding batch and non-sensitive execution metadata."""

    embeddings: tuple[tuple[float, ...], ...]
    model: str
    dimension: int
    latency_ms: float
    provider_calls: int = 1
    normalized: bool = False

    @property
    def count(self) -> int:
        """Return the number of vectors without exposing the input text."""

        return len(self.embeddings)


type EmbeddingOutcome = Literal["ok", "error"]


@dataclass(frozen=True, slots=True)
class EmbeddingMetric:
    """Bounded metric record; input text is deliberately never included."""

    provider: str
    model: str
    input_count: int
    provider_calls: int
    dimension: int
    latency_ms: float
    normalized: bool
    outcome: EmbeddingOutcome
    error_code: str | None = None
