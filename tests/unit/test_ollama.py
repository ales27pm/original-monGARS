from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any

import httpx
import pytest

from mongars.inference import (
    ChatMessage,
    EmbeddingDimensionError,
    InferenceConfigurationError,
    InferenceHTTPError,
    InferenceResponseError,
    InferenceTimeoutError,
    OllamaBackend,
)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def test_chat_uses_native_endpoint_and_normalizes_response() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            assert json.loads(request.content) == {
                "model": "qwen-chat",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.2},
            }
            return httpx.Response(
                200,
                json={
                    "model": "qwen-chat",
                    "message": {
                        "role": "assistant",
                        "content": "private reasoning</think>\n\nHello.",
                    },
                    "done": True,
                    "done_reason": "stop",
                    "prompt_eval_count": 4,
                    "eval_count": 2,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="qwen-chat",
                embedding_model="nomic-embed",
                embedding_dimension=3,
                think=False,
                client=client,
            )
            result = await backend.chat(
                [ChatMessage(role="user", content="hello")],
                options={"temperature": 0.2},
            )

        assert result.content == "Hello."
        assert result.model == "qwen-chat"
        assert result.done_reason == "stop"
        assert result.prompt_tokens == 4
        assert result.completion_tokens == 2

    run(exercise())


def test_embed_validates_configured_dimension_and_batch_count() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/embed"
            assert json.loads(request.content) == {
                "model": "nomic-embed",
                "input": ["alpha", "beta"],
            }
            return httpx.Response(
                200,
                json={
                    "model": "nomic-embed",
                    "embeddings": [[1, 2.5, 3], [4, 5, 6]],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="qwen-chat",
                embedding_model="nomic-embed",
                embedding_dimension=3,
                client=client,
            )
            result = await backend.embed(["alpha", "beta"])

        assert result.dimension == 3
        assert result.embeddings == ((1.0, 2.5, 3.0), (4.0, 5.0, 6.0))

    run(exercise())


def test_embed_call_dimension_overrides_configured_dimension() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [[1, 2]]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                embedding_dimension=3,
                client=client,
            )
            result = await backend.embed(["alpha"], expected_dimension=2)

        assert result.dimension == 2

    run(exercise())


def test_embed_rejects_a_wrong_dimension() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [[1, 2]]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                embedding_dimension=3,
                client=client,
            )
            with pytest.raises(EmbeddingDimensionError) as caught:
                await backend.embed(["alpha"])

        assert caught.value.expected == 3
        assert caught.value.actual == 2
        assert caught.value.index == 0

    run(exercise())


def test_embed_requires_dimension_from_config_or_caller() -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(500))
        async with httpx.AsyncClient(transport=transport) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            with pytest.raises(InferenceConfigurationError):
                await backend.embed(["alpha"])

    run(exercise())


def test_timeout_is_normalized() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            with pytest.raises(InferenceTimeoutError) as caught:
                await backend.chat([ChatMessage(role="user", content="hello")])

        assert caught.value.code == "timeout"
        assert caught.value.retryable is True
        assert caught.value.operation == "chat"

    run(exercise())


def test_chat_rejects_an_incomplete_response() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": "partial"}, "done": False},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            with pytest.raises(InferenceResponseError, match="not marked complete"):
                await backend.chat([ChatMessage(role="user", content="hello")])

    run(exercise())


def test_http_status_is_normalized_without_copying_response_body() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "model is loading", "secret": "nope"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            with pytest.raises(InferenceHTTPError) as caught:
                await backend.chat([ChatMessage(role="user", content="hello")])

        assert caught.value.status_code == 503
        assert caught.value.retryable is True
        assert "model is loading" in str(caught.value)
        assert "secret" not in str(caught.value)

    run(exercise())


def test_invalid_json_is_normalized() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"not-json")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            with pytest.raises(InferenceResponseError):
                await backend.chat([ChatMessage(role="user", content="hello")])

    run(exercise())


def test_health_returns_typed_success_and_failure() -> None:
    async def exercise() -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            assert request.url.path == "/api/tags"
            calls += 1
            if calls == 1:
                return httpx.Response(200, json={"models": []})
            return httpx.Response(503, json={"error": "offline"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            healthy = await backend.health()
            unhealthy = await backend.health()

        assert healthy.healthy is True
        assert healthy.error_code is None
        assert healthy.latency_ms >= 0
        assert unhealthy.healthy is False
        assert unhealthy.error_code == "http_error"

    run(exercise())
