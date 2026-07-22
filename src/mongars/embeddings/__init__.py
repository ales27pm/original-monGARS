"""Public Neurons embedding contracts and providers."""

from .base import EmbeddingProvider
from .errors import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingHTTPError,
    EmbeddingInputError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
)
from .models import EmbeddingBatch, EmbeddingMetric
from .ollama import OllamaEmbeddingProvider
from .service import EmbeddingService, MetricSink

__all__ = [
    "EmbeddingBatch",
    "EmbeddingConfigurationError",
    "EmbeddingConnectionError",
    "EmbeddingDimensionError",
    "EmbeddingError",
    "EmbeddingHTTPError",
    "EmbeddingInputError",
    "EmbeddingMetric",
    "EmbeddingModelMismatchError",
    "EmbeddingProvider",
    "EmbeddingResponseError",
    "EmbeddingService",
    "EmbeddingTimeoutError",
    "MetricSink",
    "OllamaEmbeddingProvider",
]
