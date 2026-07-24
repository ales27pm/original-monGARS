"""Public inference contracts and Ollama implementation."""

from .base import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    HealthStatus,
    InferenceBackend,
    InferenceConfigurationError,
    InferenceConnectionError,
    InferenceError,
    InferenceHTTPError,
    InferenceRequestError,
    InferenceResponseError,
    InferenceTimeoutError,
    JsonValue,
    StreamingInferenceBackend,
)
from .ollama import OllamaBackend

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "ChatStreamChunk",
    "HealthStatus",
    "InferenceBackend",
    "InferenceConfigurationError",
    "InferenceConnectionError",
    "InferenceError",
    "InferenceHTTPError",
    "InferenceRequestError",
    "InferenceResponseError",
    "InferenceTimeoutError",
    "JsonValue",
    "OllamaBackend",
    "StreamingInferenceBackend",
]
