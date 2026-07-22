"""Backend-neutral contracts for monGARS inference runtimes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
type ChatRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One normalized message exchanged with an inference backend."""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """Normalized non-streaming chat response."""

    content: str
    model: str
    done_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """A non-throwing dependency health result suitable for readiness checks."""

    backend: str
    backend_reachable: bool
    chat_model_ready: bool
    embedding_model_ready: bool
    latency_ms: float
    error_code: str | None = None

    @property
    def healthy(self) -> bool:
        """Require both connectivity and every configured mandatory model."""

        return self.backend_reachable and self.chat_model_ready and self.embedding_model_ready


class InferenceError(RuntimeError):
    """Base class for stable errors exposed across inference backends."""

    code = "inference_error"

    def __init__(
        self,
        message: str,
        *,
        backend: str,
        operation: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.backend = backend
        self.operation = operation
        self.retryable = retryable


class InferenceConfigurationError(InferenceError):
    code = "configuration_error"


class InferenceRequestError(InferenceError):
    code = "invalid_request"


class InferenceTimeoutError(InferenceError):
    code = "timeout"


class InferenceConnectionError(InferenceError):
    code = "connection_error"


class InferenceHTTPError(InferenceError):
    code = "http_error"

    def __init__(
        self,
        message: str,
        *,
        backend: str,
        operation: str,
        status_code: int,
        retryable: bool,
    ) -> None:
        super().__init__(
            message,
            backend=backend,
            operation=operation,
            retryable=retryable,
        )
        self.status_code = status_code


class InferenceResponseError(InferenceError):
    code = "invalid_response"


@runtime_checkable
class InferenceBackend(Protocol):
    """Async contract implemented by local and remote inference adapters."""

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        """Generate one non-streaming assistant response."""

    async def health(self) -> HealthStatus:
        """Probe backend availability without raising an inference error."""

    async def aclose(self) -> None:
        """Release resources owned by the adapter."""
