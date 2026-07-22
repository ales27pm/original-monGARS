"""Validated, bounded orchestration for semantic vector generation."""

from __future__ import annotations

import logging
import math
import unicodedata
from collections.abc import Callable, Sequence
from time import monotonic

from .base import EmbeddingProvider
from .errors import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
)
from .limits import (
    MAX_EMBEDDING_INPUTS,
    MAX_EMBEDDING_TEXT_CHARACTERS,
    MAX_EMBEDDING_TOTAL_CHARACTERS,
)
from .models import EmbeddingBatch, EmbeddingMetric

logger = logging.getLogger(__name__)

_SERVICE_PROVIDER = "embedding_service"
_MAX_PROVIDER_BATCH_SIZE = 128

type MetricSink = Callable[[EmbeddingMetric], None]


class EmbeddingService:
    """The sole application boundary for embedding text.

    The service performs validation before provider I/O, splits large caller batches,
    and verifies every response again. It has no persistence dependency and never
    receives owner identifiers, database sessions, or repositories.
    """

    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        expected_dimension: int,
        batch_size: int,
        normalize_vectors: bool = False,
        max_inputs: int = MAX_EMBEDDING_INPUTS,
        max_text_characters: int = MAX_EMBEDDING_TEXT_CHARACTERS,
        max_total_characters: int = MAX_EMBEDDING_TOTAL_CHARACTERS,
        metric_sink: MetricSink | None = None,
    ) -> None:
        self._provider = provider
        self._provider_name = _nonempty_identifier(
            provider.provider_name,
            field="provider_name",
        )
        self._model_name = _nonempty_identifier(provider.model_name, field="model_name")
        self._expected_dimension = _positive_int(
            expected_dimension,
            field="expected_dimension",
        )
        self._batch_size = _bounded_positive_int(
            batch_size,
            field="batch_size",
            maximum=_MAX_PROVIDER_BATCH_SIZE,
        )
        self._max_inputs = _bounded_positive_int(
            max_inputs,
            field="max_inputs",
            maximum=MAX_EMBEDDING_INPUTS,
        )
        self._max_text_characters = _bounded_positive_int(
            max_text_characters,
            field="max_text_characters",
            maximum=MAX_EMBEDDING_TEXT_CHARACTERS,
        )
        self._max_total_characters = _bounded_positive_int(
            max_total_characters,
            field="max_total_characters",
            maximum=MAX_EMBEDDING_TOTAL_CHARACTERS,
        )
        if self._max_total_characters < self._max_text_characters:
            raise EmbeddingConfigurationError(
                "max_total_characters must be at least max_text_characters.",
                provider=_SERVICE_PROVIDER,
            )
        self._normalize_vectors = normalize_vectors
        self._metric_sink = metric_sink

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._expected_dimension

    @property
    def max_inputs(self) -> int:
        """Return the maximum number of texts accepted by one logical request."""

        return self._max_inputs

    @property
    def max_text_characters(self) -> int:
        """Return the validated per-text character ceiling."""

        return self._max_text_characters

    @property
    def max_total_characters(self) -> int:
        """Return the aggregate character ceiling for one logical request."""

        return self._max_total_characters

    async def aclose(self) -> None:
        """Release resources owned by the configured provider."""

        await self._provider.aclose()

    async def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        """Normalize, bound, split, and validate one logical embedding request."""

        normalized = _normalize_texts(
            texts,
            provider=self._provider_name,
            max_inputs=self._max_inputs,
            max_text_characters=self._max_text_characters,
            max_total_characters=self._max_total_characters,
        )
        started = monotonic()
        provider_calls = 0
        try:
            self._verify_provider_identity()
            vectors: list[tuple[float, ...]] = []
            for offset in range(0, len(normalized), self._batch_size):
                batch_texts = normalized[offset : offset + self._batch_size]
                response = await self._provider.embed(
                    batch_texts,
                    expected_dimension=self._expected_dimension,
                )
                provider_calls += 1
                self._verify_provider_identity()
                vectors.extend(
                    _validate_provider_batch(
                        response,
                        provider=self._provider_name,
                        expected_model=self._model_name,
                        expected_dimension=self._expected_dimension,
                        expected_count=len(batch_texts),
                        index_offset=offset,
                        normalize_vectors=self._normalize_vectors,
                    )
                )
        except Exception as exc:
            elapsed_ms = (monotonic() - started) * 1_000
            self._emit_metric(
                EmbeddingMetric(
                    provider=self._provider_name,
                    model=self._model_name,
                    input_count=len(normalized),
                    provider_calls=provider_calls,
                    dimension=self._expected_dimension,
                    latency_ms=elapsed_ms,
                    normalized=self._normalize_vectors,
                    outcome="error",
                    error_code=(
                        exc.code if isinstance(exc, EmbeddingError) else "unexpected_error"
                    ),
                )
            )
            raise

        elapsed_ms = (monotonic() - started) * 1_000
        result = EmbeddingBatch(
            embeddings=tuple(vectors),
            model=self._model_name,
            dimension=self._expected_dimension,
            latency_ms=elapsed_ms,
            provider_calls=provider_calls,
            normalized=self._normalize_vectors,
        )
        self._emit_metric(
            EmbeddingMetric(
                provider=self._provider_name,
                model=self._model_name,
                input_count=len(normalized),
                provider_calls=provider_calls,
                dimension=self._expected_dimension,
                latency_ms=elapsed_ms,
                normalized=self._normalize_vectors,
                outcome="ok",
            )
        )
        return result

    def _verify_provider_identity(self) -> None:
        current_provider = _nonempty_identifier(
            self._provider.provider_name,
            field="provider_name",
        )
        current_model = _nonempty_identifier(self._provider.model_name, field="model_name")
        if current_provider != self._provider_name:
            raise EmbeddingConfigurationError(
                "Embedding provider identity changed after service construction.",
                provider=self._provider_name,
            )
        if current_model != self._model_name:
            raise EmbeddingModelMismatchError(
                provider=self._provider_name,
                expected=self._model_name,
                actual=current_model,
            )

    def _emit_metric(self, metric: EmbeddingMetric) -> None:
        if self._metric_sink is not None:
            self._metric_sink(metric)
        level = logging.INFO if metric.outcome == "ok" else logging.WARNING
        logger.log(
            level,
            "embedding_batch_%s",
            "completed" if metric.outcome == "ok" else "failed",
            extra={
                "embedding_provider": metric.provider,
                "embedding_model": metric.model,
                "embedding_input_count": metric.input_count,
                "embedding_provider_calls": metric.provider_calls,
                "embedding_dimension": metric.dimension,
                "embedding_latency_ms": round(metric.latency_ms, 2),
                "embedding_normalized": metric.normalized,
                "embedding_outcome": metric.outcome,
                "embedding_error_code": metric.error_code,
            },
        )


def _normalize_texts(
    texts: Sequence[str],
    *,
    provider: str,
    max_inputs: int,
    max_text_characters: int,
    max_total_characters: int,
) -> tuple[str, ...]:
    if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
        raise EmbeddingInputError(
            "Embedding input must be a sequence of strings.",
            provider=provider,
        )
    if not texts:
        raise EmbeddingInputError(
            "Embedding input must contain at least one text.",
            provider=provider,
        )
    if len(texts) > max_inputs:
        raise EmbeddingInputError(
            f"Embedding input exceeds the {max_inputs}-item limit.",
            provider=provider,
        )

    normalized: list[str] = []
    total_characters = 0
    for index, text in enumerate(texts):
        if not isinstance(text, str):
            raise EmbeddingInputError(
                f"Embedding input {index} is not a string.",
                provider=provider,
            )
        value = unicodedata.normalize(
            "NFC",
            text.replace("\r\n", "\n").replace("\r", "\n"),
        ).strip()
        if not value:
            raise EmbeddingInputError(
                f"Embedding input {index} is empty after normalization.",
                provider=provider,
            )
        if len(value) > max_text_characters:
            raise EmbeddingInputError(
                f"Embedding input {index} exceeds the {max_text_characters}-character limit.",
                provider=provider,
            )
        total_characters += len(value)
        if total_characters > max_total_characters:
            raise EmbeddingInputError(
                (f"Embedding input exceeds the {max_total_characters}-character aggregate limit."),
                provider=provider,
            )
        normalized.append(value)
    return tuple(normalized)


def _validate_provider_batch(
    response: EmbeddingBatch,
    *,
    provider: str,
    expected_model: str,
    expected_dimension: int,
    expected_count: int,
    index_offset: int,
    normalize_vectors: bool,
) -> tuple[tuple[float, ...], ...]:
    if not isinstance(response, EmbeddingBatch):
        raise EmbeddingResponseError(
            "Embedding provider returned an unsupported response type.",
            provider=provider,
        )
    if response.model != expected_model:
        raise EmbeddingModelMismatchError(
            provider=provider,
            expected=expected_model,
            actual=response.model,
        )
    if response.dimension != expected_dimension:
        raise EmbeddingDimensionError(
            provider=provider,
            expected=expected_dimension,
            actual=response.dimension,
            index=index_offset,
        )
    if len(response.embeddings) != expected_count:
        raise EmbeddingResponseError(
            (
                "Embedding response count does not match its input count: "
                f"received {len(response.embeddings)}, expected {expected_count}."
            ),
            provider=provider,
        )
    if not math.isfinite(response.latency_ms) or response.latency_ms < 0:
        raise EmbeddingResponseError(
            "Embedding provider returned invalid latency metadata.",
            provider=provider,
        )

    vectors: list[tuple[float, ...]] = []
    for relative_index, raw_vector in enumerate(response.embeddings):
        absolute_index = index_offset + relative_index
        if len(raw_vector) != expected_dimension:
            raise EmbeddingDimensionError(
                provider=provider,
                expected=expected_dimension,
                actual=len(raw_vector),
                index=absolute_index,
            )
        vector: list[float] = []
        for component in raw_vector:
            if (
                isinstance(component, bool)
                or not isinstance(component, (int, float))
                or not math.isfinite(component)
            ):
                raise EmbeddingResponseError(
                    f"Embedding {absolute_index} contains a non-finite component.",
                    provider=provider,
                )
            vector.append(float(component))
        if normalize_vectors:
            magnitude = math.sqrt(sum(component * component for component in vector))
            if not math.isfinite(magnitude) or magnitude == 0:
                raise EmbeddingResponseError(
                    f"Embedding {absolute_index} cannot be L2-normalized.",
                    provider=provider,
                )
            vector = [component / magnitude for component in vector]
        vectors.append(tuple(vector))
    return tuple(vectors)


def _nonempty_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise EmbeddingConfigurationError(
            f"{field} must be a non-empty trimmed string.",
            provider=_SERVICE_PROVIDER,
        )
    return value


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise EmbeddingConfigurationError(
            f"{field} must be a positive integer.",
            provider=_SERVICE_PROVIDER,
        )
    return value


def _bounded_positive_int(value: object, *, field: str, maximum: int) -> int:
    normalized = _positive_int(value, field=field)
    if normalized > maximum:
        raise EmbeddingConfigurationError(
            f"{field} must be at most {maximum}.",
            provider=_SERVICE_PROVIDER,
        )
    return normalized
