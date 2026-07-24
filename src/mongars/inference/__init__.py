"""Public inference contracts and Ollama implementations."""

from mongars.inference.base import (
    ChatMessage,
    ChatResponse,
    ChatStreamCompleted,
    ChatStreamDelta,
    ChatStreamEvent,
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
from mongars.inference.ollama import OllamaBackend
from mongars.inference.ollama_streaming import StreamingOllamaBackend
from mongars.inference.streaming import ObservedStreamingInference

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "ChatStreamCompleted",
    "ChatStreamDelta",
    "ChatStreamEvent",
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
    "ObservedStreamingInference",
    "OllamaBackend",
    "StreamingInferenceBackend",
    "StreamingOllamaBackend",
]
