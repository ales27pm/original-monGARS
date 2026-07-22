"""Public inference contracts and Ollama implementation."""

from .base import (
    ChatMessage,
    ChatResponse,
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
)
from .ollama import OllamaBackend

__all__ = [
    "ChatMessage",
    "ChatResponse",
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
]
