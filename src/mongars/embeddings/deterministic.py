"""Deterministic provider used by contract, integration, and adversarial tests."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from .errors import EmbeddingConfigurationError, EmbeddingInputError
from .models import EmbeddingBatch, validate_model_digest

_PROVIDER = "deterministic"


class DeterministicEmbeddingProvider:
    """Generate stable finite vectors without network or model dependencies."""

    def __init__(
        self,
        *,
        model: str = "deterministic-embedding-v1",
        dimension: int = 8,
        model_digest: str | None = None,
    ) -> None:
        if not isinstance(model, str) or not model.strip() or model != model.strip():
            raise EmbeddingConfigurationError(
                "Deterministic embedding model must be a non-empty trimmed string.",
                provider=_PROVIDER,
            )
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension < 1:
            raise EmbeddingConfigurationError(
                "Deterministic embedding dimension must be positive.",
                provider=_PROVIDER,
            )
        derived_digest = hashlib.sha256(f"{model}\0{dimension}".encode()).hexdigest()
        try:
            self._model_digest = validate_model_digest(model_digest or derived_digest)
        except ValueError as exc:
            raise EmbeddingConfigurationError(
                "Deterministic embedding digest must be a SHA-256 digest.",
                provider=_PROVIDER,
            ) from exc
        self._model = model
        self._dimension = dimension
        self.calls: list[tuple[str, ...]] = []
        self.closed = False

    @property
    def provider_name(self) -> str:
        return _PROVIDER

    @property
    def model_name(self) -> str:
        return self._model

    async def resolve_model_digest(self) -> str:
        return self._model_digest

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        if expected_dimension != self._dimension:
            raise EmbeddingConfigurationError(
                "Requested dimension does not match the deterministic provider.",
                provider=_PROVIDER,
            )
        if isinstance(texts, (str, bytes)) or not texts:
            raise EmbeddingInputError(
                "Deterministic input must be a non-empty sequence.",
                provider=_PROVIDER,
            )
        prepared = tuple(texts)
        if any(not isinstance(text, str) or not text for text in prepared):
            raise EmbeddingInputError(
                "Deterministic inputs must be non-empty strings.",
                provider=_PROVIDER,
            )
        self.calls.append(prepared)
        return EmbeddingBatch(
            embeddings=tuple(self._vector(text) for text in prepared),
            model=self._model,
            model_digest=self._model_digest,
            dimension=self._dimension,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        self.closed = True

    def _vector(self, text: str) -> tuple[float, ...]:
        components: list[float] = []
        counter = 0
        while len(components) < self._dimension:
            digest = hashlib.sha256(
                self._model_digest.encode() + b"\0" + text.encode("utf-8") + counter.to_bytes(4)
            ).digest()
            for offset in range(0, len(digest), 8):
                integer = int.from_bytes(digest[offset : offset + 8], "big")
                components.append((integer / ((1 << 64) - 1)) * 2.0 - 1.0)
                if len(components) == self._dimension:
                    break
            counter += 1
        return tuple(components)
