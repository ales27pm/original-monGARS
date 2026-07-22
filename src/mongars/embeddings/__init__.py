"""Public Neurons embedding contracts and providers."""

from .base import EmbeddingProvider
from .deterministic import DeterministicEmbeddingProvider
from .errors import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingContextError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingHTTPError,
    EmbeddingInputError,
    EmbeddingModelDigestMismatchError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
)
from .models import (
    EmbeddingBatch,
    EmbeddingMetric,
    EmbeddingProfile,
    EmbeddingPurpose,
    EmbeddingSpace,
    NormalizationPolicy,
)
from .ollama import OllamaEmbeddingProvider
from .service import EmbeddingService, MetricSink

__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingBatch",
    "EmbeddingConfigurationError",
    "EmbeddingConnectionError",
    "EmbeddingContextError",
    "EmbeddingDimensionError",
    "EmbeddingError",
    "EmbeddingHTTPError",
    "EmbeddingInputError",
    "EmbeddingMetric",
    "EmbeddingModelDigestMismatchError",
    "EmbeddingModelMismatchError",
    "EmbeddingProfile",
    "EmbeddingProvider",
    "EmbeddingPurpose",
    "EmbeddingResponseError",
    "EmbeddingService",
    "EmbeddingSpace",
    "EmbeddingTimeoutError",
    "MetricSink",
    "NormalizationPolicy",
    "OllamaEmbeddingProvider",
]
