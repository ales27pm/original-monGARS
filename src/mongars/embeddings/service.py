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
    EmbeddingContextError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingInputError,
    EmbeddingModelDigestMismatchError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
)
from .limits import (
    MAX_EMBEDDING_INPUTS,
    MAX_EMBEDDING_TEXT_BYTES,
    MAX_EMBEDDING_TEXT_CHARACTERS,
    MAX_EMBEDDING_TOTAL_BYTES,
    MAX_EMBEDDING_TOTAL_CHARACTERS,
)
from .models import (
    EmbeddingBatch,
    EmbeddingMetric,
    EmbeddingProfile,
    EmbeddingPurpose,
    EmbeddingSpace,
    NormalizationPolicy,
    validate_embedding_purpose,
    validate_model_digest,
)

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
        max_text_bytes: int = MAX_EMBEDDING_TEXT_BYTES,
        max_total_bytes: int = MAX_EMBEDDING_TOTAL_BYTES,
        profile: EmbeddingProfile | None = None,
        expected_model_digest: str | None = None,
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
        self._max_text_bytes = _bounded_positive_int(
            max_text_bytes,
            field="max_text_bytes",
            maximum=MAX_EMBEDDING_TEXT_BYTES,
        )
        self._max_total_bytes = _bounded_positive_int(
            max_total_bytes,
            field="max_total_bytes",
            maximum=MAX_EMBEDDING_TOTAL_BYTES,
        )
        if self._max_total_bytes < self._max_text_bytes:
            raise EmbeddingConfigurationError(
                "max_total_bytes must be at least max_text_bytes.",
                provider=_SERVICE_PROVIDER,
            )
        self._normalize_vectors = normalize_vectors
        self._profile = profile or EmbeddingProfile()
        try:
            self._expected_model_digest = (
                validate_model_digest(expected_model_digest)
                if expected_model_digest is not None
                else None
            )
        except ValueError as exc:
            raise EmbeddingConfigurationError(
                "expected_model_digest must be a SHA-256 artifact digest.",
                provider=_SERVICE_PROVIDER,
            ) from exc
        self._metric_sink = metric_sink
        self._pinned_space: EmbeddingSpace | None = None

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

    @property
    def max_text_bytes(self) -> int:
        """Return the prepared per-input UTF-8 byte ceiling."""

        return self._max_text_bytes

    @property
    def max_total_bytes(self) -> int:
        """Return the prepared aggregate UTF-8 byte ceiling."""

        return self._max_total_bytes

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    @property
    def embedding_space(self) -> EmbeddingSpace | None:
        """Return the pinned space after its artifact digest has been resolved."""

        return self._pinned_space

    async def aclose(self) -> None:
        """Release resources owned by the configured provider."""

        await self._provider.aclose()

    async def resolve_space(self) -> EmbeddingSpace:
        """Resolve the mutable model alias and pin one immutable vector space."""

        self._verify_provider_identity()
        try:
            digest = validate_model_digest(await self._provider.resolve_model_digest())
        except ValueError as exc:
            raise EmbeddingConfigurationError(
                "Embedding provider returned an invalid artifact digest.",
                provider=self._provider_name,
            ) from exc
        candidate = EmbeddingSpace.from_profile(
            provider=self._provider_name,
            model_alias=self._model_name,
            model_digest=digest,
            dimension=self._expected_dimension,
            normalization_policy=self.normalization_policy,
            maximum_input_bytes=self._max_text_bytes,
            profile=self._profile,
        )
        if (
            self._expected_model_digest is not None
            and candidate.model_digest != self._expected_model_digest
        ):
            raise EmbeddingModelDigestMismatchError(
                provider=self._provider_name,
                expected=self._expected_model_digest,
                actual=candidate.model_digest,
            )
        if self._pinned_space is None:
            self._pinned_space = candidate
        elif self._pinned_space != candidate:
            raise EmbeddingModelDigestMismatchError(
                provider=self._provider_name,
                expected=self._pinned_space.model_digest,
                actual=candidate.model_digest,
            )
        return self._pinned_space

    @property
    def normalization_policy(self) -> NormalizationPolicy:
        return "l2" if self._normalize_vectors else "none"

    async def embed(
        self,
        texts: Sequence[str],
        *,
        purpose: EmbeddingPurpose,
    ) -> EmbeddingBatch:
        """Normalize, bound, split, and validate one logical embedding request."""

        try:
            validated_purpose = validate_embedding_purpose(purpose)
        except ValueError as exc:
            raise EmbeddingInputError(str(exc), provider=self._provider_name) from exc
        normalized = _normalize_texts(
            texts,
            provider=self._provider_name,
            max_inputs=self._max_inputs,
            max_text_characters=self._max_text_characters,
            max_total_characters=self._max_total_characters,
        )
        prepared, input_bytes = _prepare_texts(
            normalized,
            purpose=validated_purpose,
            profile=self._profile,
            provider=self._provider_name,
            max_text_bytes=self._max_text_bytes,
            max_total_bytes=self._max_total_bytes,
        )
        started = monotonic()
        provider_calls = 0
        space: EmbeddingSpace | None = None
        try:
            space = await self.resolve_space()
            if space is None:
                raise EmbeddingConfigurationError(
                    "Embedding-space resolution returned no identity.",
                    provider=self._provider_name,
                )
            vectors: list[tuple[float, ...]] = []
            for offset in range(0, len(prepared), self._batch_size):
                batch_texts = prepared[offset : offset + self._batch_size]
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
                        expected_model_digest=space.model_digest,
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
                    purpose=validated_purpose,
                    embedding_space_id=space.space_id if space is not None else None,
                    input_bytes=input_bytes,
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
            model_digest=space.model_digest,
            embedding_space_id=space.space_id,
            purpose=validated_purpose,
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
                purpose=validated_purpose,
                embedding_space_id=space.space_id,
                input_bytes=input_bytes,
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
            try:
                self._metric_sink(metric)
            except Exception:
                logger.warning(
                    "embedding_metric_sink_failed",
                    extra={
                        "embedding_provider": metric.provider,
                        "embedding_model": metric.model,
                        "embedding_outcome": metric.outcome,
                    },
                )
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
                "embedding_purpose": metric.purpose,
                "embedding_space_id": metric.embedding_space_id,
                "embedding_input_bytes": metric.input_bytes,
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


def _prepare_texts(
    texts: tuple[str, ...],
    *,
    purpose: EmbeddingPurpose,
    profile: EmbeddingProfile,
    provider: str,
    max_text_bytes: int,
    max_total_bytes: int,
) -> tuple[tuple[str, ...], int]:
    prefix = profile.instruction_for(purpose)
    prepared: list[str] = []
    total_bytes = 0
    for index, text in enumerate(texts):
        value = f"{prefix}{text}"
        byte_count = len(value.encode("utf-8"))
        if byte_count > max_text_bytes:
            raise EmbeddingContextError(
                (
                    f"Prepared embedding input {index} exceeds the reviewed "
                    f"{max_text_bytes}-byte context ceiling."
                ),
                provider=provider,
                maximum_input_bytes=max_text_bytes,
                input_bytes=byte_count,
                input_index=index,
            )
        total_bytes += byte_count
        if total_bytes > max_total_bytes:
            raise EmbeddingInputError(
                f"Prepared embedding input exceeds the {max_total_bytes}-byte aggregate limit.",
                provider=provider,
            )
        prepared.append(value)
    return tuple(prepared), total_bytes


def _validate_provider_batch(
    response: EmbeddingBatch,
    *,
    provider: str,
    expected_model: str,
    expected_model_digest: str,
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
    if response.model_digest is None:
        raise EmbeddingResponseError(
            "Embedding provider response has no artifact digest.",
            provider=provider,
        )
    try:
        response_digest = validate_model_digest(response.model_digest)
    except ValueError as exc:
        raise EmbeddingResponseError(
            "Embedding provider response has an invalid artifact digest.",
            provider=provider,
        ) from exc
    if response_digest != expected_model_digest:
        raise EmbeddingModelDigestMismatchError(
            provider=provider,
            expected=expected_model_digest,
            actual=response_digest,
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
        magnitude = math.hypot(*vector)
        if not math.isfinite(magnitude):
            raise EmbeddingResponseError(
                f"Embedding {absolute_index} has a non-finite magnitude.",
                provider=provider,
            )
        if magnitude == 0:
            raise EmbeddingResponseError(
                (
                    f"Embedding {absolute_index} has zero magnitude; it cannot be "
                    "L2-normalized or used for cosine retrieval."
                ),
                provider=provider,
            )
        if normalize_vectors:
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
