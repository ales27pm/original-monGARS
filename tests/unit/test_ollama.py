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
                        "content": "<think>private reasoning</think>\n\nHello.",
                        "thinking": "This separate field must not become user-visible.",
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


def test_owned_client_ignores_proxy_environment_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_options: dict[str, object] = {}
    requested_urls: list[str] = []

    class _Client:
        def __init__(self, **kwargs: object) -> None:
            constructor_options.update(kwargs)

        async def request(self, method: str, url: str, **_kwargs: object) -> httpx.Response:
            assert method == "POST"
            requested_urls.append(url)
            return httpx.Response(
                200,
                json={
                    "model": "chat",
                    "message": {"role": "assistant", "content": "local answer"},
                    "done": True,
                    "done_reason": "stop",
                },
            )

        async def aclose(self) -> None:
            return None

    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr("mongars.inference.ollama.httpx.AsyncClient", _Client)

    async def exercise() -> None:
        backend = OllamaBackend(
            base_url="http://ollama:11434",
            chat_model="chat",
            embedding_model="embed",
        )
        response = await backend.chat([ChatMessage(role="user", content="hello")])
        await backend.aclose()
        assert response.content == "local answer"

    run(exercise())

    assert constructor_options == {"trust_env": False, "follow_redirects": False}
    assert requested_urls == ["http://ollama:11434/api/chat"]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("<think>unfinished reasoning", "residual thinking marker"),
        ("Answer.</think>", "residual thinking marker"),
        ("Answer <think>hidden trace</think>", "residual thinking marker"),
        ("<think>first</think><think>second</think>Answer.", "residual thinking marker"),
        ("<think>only reasoning</think>\n\t", "empty content"),
        (" \n\t", "empty content"),
    ],
)
def test_chat_rejects_thinking_markers_and_empty_content(
    content: str,
    error: str,
) -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "qwen-chat",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "thinking": "Never use this field as response content.",
                    },
                    "done": True,
                    "done_reason": "stop",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="qwen-chat",
                embedding_model="nomic-embed",
                think=False,
                client=client,
            )
            with pytest.raises(InferenceResponseError, match=error):
                await backend.chat([ChatMessage(role="user", content="hello")])

    run(exercise())


def test_chat_rejects_a_generation_truncated_by_length() -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "qwen-chat",
                    "message": {"role": "assistant", "content": "A plausible partial answer"},
                    "done": True,
                    "done_reason": "length",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="qwen-chat",
                embedding_model="nomic-embed",
                client=client,
            )
            with pytest.raises(InferenceResponseError, match="truncated"):
                await backend.chat([ChatMessage(role="user", content="hello")])

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


def test_health_requires_both_configured_models_and_canonicalizes_names() -> None:
    async def exercise() -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            assert request.url.path == "/api/tags"
            calls += 1
            if calls == 1:
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "name": "registry.ollama.ai/library/qwen-chat:latest",
                            },
                            {"model": "nomic-embed:latest"},
                        ]
                    },
                )
            if calls == 2:
                return httpx.Response(200, json={"models": [{"name": "qwen-chat"}]})
            if calls == 3:
                return httpx.Response(200, json={"models": [{"name": "unrelated:latest"}]})
            return httpx.Response(503, json={"error": "offline"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="qwen-chat",
                embedding_model="nomic-embed",
                client=client,
            )
            ready = await backend.health()
            embedding_missing = await backend.health()
            unrelated = await backend.health()
            unavailable = await backend.health()

        assert ready.healthy is True
        assert ready.backend_reachable is True
        assert ready.chat_model_ready is True
        assert ready.embedding_model_ready is True
        assert ready.error_code is None
        assert ready.latency_ms >= 0

        assert embedding_missing.healthy is False
        assert embedding_missing.backend_reachable is True
        assert embedding_missing.chat_model_ready is True
        assert embedding_missing.embedding_model_ready is False
        assert embedding_missing.error_code == "required_models_missing"

        assert unrelated.healthy is False
        assert unrelated.backend_reachable is True
        assert unrelated.chat_model_ready is False
        assert unrelated.embedding_model_ready is False
        assert unrelated.error_code == "required_models_missing"

        assert unavailable.healthy is False
        assert unavailable.backend_reachable is True
        assert unavailable.chat_model_ready is False
        assert unavailable.embedding_model_ready is False
        assert unavailable.error_code == "http_error"

    run(exercise())


def test_health_does_not_collapse_unrelated_model_namespaces() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/tags"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "registry.example/attacker/qwen3:4b"},
                        {"name": "registry.example/attacker/embed:latest"},
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="registry.example/acme/qwen3:4b",
                embedding_model="registry.example/acme/embed:latest",
                client=client,
            )
            health = await backend.health()

        assert health.backend_reachable is True
        assert health.chat_model_ready is False
        assert health.embedding_model_ready is False
        assert health.healthy is False
        assert health.error_code == "required_models_missing"

    run(exercise())


def test_health_reports_connection_failure_as_unreachable() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = OllamaBackend(
                base_url="http://ollama:11434",
                chat_model="chat",
                embedding_model="embed",
                client=client,
            )
            result = await backend.health()

        assert result.healthy is False
        assert result.backend_reachable is False
        assert result.chat_model_ready is False
        assert result.embedding_model_ready is False
        assert result.error_code == "connection_error"

    run(exercise())
