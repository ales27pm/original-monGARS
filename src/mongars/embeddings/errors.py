"""Stable failures raised by the semantic-processing boundary."""

from __future__ import annotations


class EmbeddingError(RuntimeError):
    """Base error for embedding providers and the embedding service."""

    code = "embedding_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class EmbeddingConfigurationError(EmbeddingError):
    """The provider or service was configured inconsistently."""

    code = "embedding_configuration_error"


class EmbeddingInputError(EmbeddingError):
    """Caller-supplied text exceeds a validated service boundary."""

    code = "embedding_invalid_input"


class EmbeddingConnectionError(EmbeddingError):
    """The configured provider could not be reached."""

    code = "embedding_connection_error"


class EmbeddingTimeoutError(EmbeddingError):
    """The configured provider did not respond within its deadline."""

    code = "embedding_timeout"


class EmbeddingHTTPError(EmbeddingError):
    """The provider returned a non-success HTTP response."""

    code = "embedding_http_error"

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int,
        retryable: bool,
    ) -> None:
        super().__init__(message, provider=provider, retryable=retryable)
        self.status_code = status_code


class EmbeddingResponseError(EmbeddingError):
    """The provider response could not be trusted or normalized."""

    code = "embedding_invalid_response"


class EmbeddingDimensionError(EmbeddingResponseError):
    """A returned vector does not match the configured schema dimension."""

    code = "embedding_dimension_mismatch"

    def __init__(
        self,
        *,
        provider: str,
        expected: int,
        actual: int,
        index: int,
    ) -> None:
        super().__init__(
            f"Embedding {index} has dimension {actual}; expected {expected}.",
            provider=provider,
        )
        self.expected = expected
        self.actual = actual
        self.index = index


class EmbeddingModelMismatchError(EmbeddingResponseError):
    """The provider returned vectors from a model other than the reviewed model."""

    code = "embedding_model_mismatch"

    def __init__(self, *, provider: str, expected: str, actual: str) -> None:
        super().__init__(
            f"Embedding provider returned model {actual!r}; expected {expected!r}.",
            provider=provider,
        )
        self.expected = expected
        self.actual = actual
