"""Backend-neutral contracts for semantic vector generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .models import EmbeddingBatch


@runtime_checkable
class EmbeddingProvider(Protocol):
    """A provider fixed to one reviewed embedding model."""

    @property
    def provider_name(self) -> str:
        """Return a stable provider identifier suitable for metrics."""

    @property
    def model_name(self) -> str:
        """Return the exact configured model; implementations must not auto-route."""

    async def resolve_model_digest(self) -> str:
        """Resolve and pin the configured alias to one immutable artifact digest."""

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        """Embed one bounded batch using the configured model only."""

    async def aclose(self) -> None:
        """Release resources owned by the provider."""
