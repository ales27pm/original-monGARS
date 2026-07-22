from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest

from mongars.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingConnectionError,
    EmbeddingContextError,
    EmbeddingDimensionError,
    EmbeddingHTTPError,
    EmbeddingModelDigestMismatchError,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
    EmbeddingTimeoutError,
    OllamaEmbeddingProvider,
)

_MODEL_DIGEST = "a" * 64


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def ollama_transport(
    embed_handler: Callable[[httpx.Request], httpx.Response],
    *,
    model: str = "nomic-embed-text",
    digest: str = _MODEL_DIGEST,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": f"{model}:latest" if ":" not in model else model,
                            "model": f"{model}:latest" if ":" not in model else model,
                            "digest": digest,
                        }
                    ]
                },
            )
        return embed_handler(request)

    return httpx.MockTransport(handler)


def test_ollama_provider_uses_fixed_model_and_native_embed_endpoint() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.url == "http://ollama:11434/api/embed"
            assert json.loads(request.content) == {
                "model": "nomic-embed-text",
                "input": ["alpha", "beta"],
                "truncate": False,
            }
            return httpx.Response(
                200,
                json={
                    "model": "nomic-embed-text",
                    "embeddings": [[1, 0, 0], [0.25, 0.5, 0.75]],
                },
            )

        async with httpx.AsyncClient(transport=ollama_transport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434/",
                model="nomic-embed-text",
                dimension=3,
                client=client,
            )
            result = await provider.embed(["alpha", "beta"], expected_dimension=3)

        assert result.embeddings == ((1.0, 0.0, 0.0), (0.25, 0.5, 0.75))
        assert result.model == "nomic-embed-text"
        assert result.model_digest == _MODEL_DIGEST
        assert result.dimension == 3
        assert result.count == 2
        assert result.latency_ms >= 0

    run(exercise())


def test_owned_client_ignores_environment_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_options: dict[str, object] = {}

    class Client:
        def __init__(self, **kwargs: object) -> None:
            constructor_options.update(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr("mongars.embeddings.ollama.httpx.AsyncClient", Client)

    async def exercise() -> None:
        provider = OllamaEmbeddingProvider(base_url="http://ollama:11434")
        await provider.aclose()

    run(exercise())
    assert constructor_options == {"trust_env": False, "follow_redirects": False}


def test_rejects_oversized_content_length_before_reading_body() -> None:
    class UnreadableStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            raise AssertionError("oversized response body must not be read")
            yield b""  # pragma: no cover

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(
                lambda _request: httpx.Response(
                    200,
                    headers={"content-length": "1025"},
                    stream=UnreadableStream(),
                )
            )
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                max_response_bytes=1_024,
                client=client,
            )
            with pytest.raises(EmbeddingResponseError, match="size limit"):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


def test_rejects_oversized_chunked_body_before_json_decode() -> None:
    class ChunkedStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield b"{" + (b"x" * 700)
            yield b"y" * 400

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(lambda _request: httpx.Response(200, stream=ChunkedStream()))
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                max_response_bytes=1_024,
                client=client,
            )
            with pytest.raises(EmbeddingResponseError, match="size limit"):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"embeddings": [[1.0, 2.0]]}, EmbeddingResponseError),
        (
            {"model": "another-model", "embeddings": [[1.0, 2.0]]},
            EmbeddingModelMismatchError,
        ),
        ({"model": "nomic", "embeddings": "bad"}, EmbeddingResponseError),
        ({"model": "nomic", "embeddings": []}, EmbeddingResponseError),
        ({"model": "nomic", "embeddings": [[1.0]]}, EmbeddingDimensionError),
    ],
)
def test_rejects_untrusted_provider_responses(
    payload: dict[str, object],
    error: type[Exception],
) -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(
                lambda _request: httpx.Response(200, json=payload),
                model="nomic",
            )
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                model="nomic",
                dimension=2,
                client=client,
            )
            with pytest.raises(error):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


def test_rejects_non_finite_component_from_provider_json() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(
                lambda _request: httpx.Response(
                    200,
                    content=b'{"model":"nomic","embeddings":[[1.0,NaN]]}',
                    headers={"content-type": "application/json"},
                ),
                model="nomic",
            )
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                model="nomic",
                dimension=2,
                client=client,
            )
            with pytest.raises(EmbeddingResponseError, match="invalid component"):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


def test_dimension_mismatch_is_rejected_before_network_io() -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=768,
                client=client,
            )
            with pytest.raises(EmbeddingConfigurationError, match="fixed dimension 768"):
                await provider.embed(["alpha"], expected_dimension=384)

    run(exercise())
    assert requests == 0


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(400, False), (429, True), (500, True)],
)
def test_maps_http_errors_without_reflecting_response_body(
    status_code: int,
    retryable: bool,
) -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(
                lambda _request: httpx.Response(status_code, text="secret provider body")
            )
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                client=client,
            )
            with pytest.raises(EmbeddingHTTPError) as caught:
                await provider.embed(["alpha"], expected_dimension=2)
            assert caught.value.status_code == status_code
            assert caught.value.retryable is retryable
            assert "secret provider body" not in str(caught.value)

    run(exercise())


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (httpx.ReadTimeout("slow"), EmbeddingTimeoutError),
        (httpx.ConnectError("offline"), EmbeddingConnectionError),
    ],
)
def test_maps_transport_failures(
    failure: Exception,
    error: type[Exception],
) -> None:
    async def exercise() -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise failure

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                client=client,
            )
            with pytest.raises(error):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


def test_rejects_input_over_conservative_utf8_byte_limit_before_network() -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                max_input_bytes=256,
                client=client,
            )
            with pytest.raises(EmbeddingContextError) as caught:
                await provider.embed(["é" * 129], expected_dimension=2)
            assert caught.value.input_bytes == 258

    run(exercise())
    assert requests == 0


def test_maps_explicit_ollama_context_rejection_without_enabling_truncation() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=ollama_transport(
                lambda _request: httpx.Response(
                    400,
                    json={"error": "input length exceeds the context length"},
                )
            )
        ) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                client=client,
            )
            with pytest.raises(EmbeddingContextError, match="exceeds its context"):
                await provider.embed(["alpha"], expected_dimension=2)

    run(exercise())


def test_model_alias_is_pinned_to_immutable_digest() -> None:
    tag_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tag_calls
        assert request.url.path == "/api/tags"
        tag_calls += 1
        digest = _MODEL_DIGEST if tag_calls == 1 else "b" * 64
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "nomic-embed-text:latest",
                        "digest": digest,
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                client=client,
            )
            assert await provider.resolve_model_digest() == _MODEL_DIGEST
            with pytest.raises(EmbeddingModelDigestMismatchError) as caught:
                await provider.resolve_model_digest()
            assert caught.value.expected == _MODEL_DIGEST
            assert caught.value.actual == "b" * 64

    run(exercise())


def test_rejects_model_alias_drift_during_embedding_request() -> None:
    requests: list[str] = []
    tag_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal tag_calls
        requests.append(request.url.path)
        if request.url.path == "/api/embed":
            return httpx.Response(
                200,
                json={
                    "model": "nomic-embed-text",
                    "embeddings": [[1.0, 0.0]],
                },
            )
        assert request.url.path == "/api/tags"
        tag_calls += 1
        digest = _MODEL_DIGEST if tag_calls == 1 else "b" * 64
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "nomic-embed-text:latest",
                        "digest": digest,
                    }
                ]
            },
        )

    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(
                base_url="http://ollama:11434",
                dimension=2,
                client=client,
            )
            with pytest.raises(EmbeddingModelDigestMismatchError) as caught:
                await provider.embed(["alpha"], expected_dimension=2)
            assert caught.value.expected == _MODEL_DIGEST
            assert caught.value.actual == "b" * 64

    run(exercise())
    assert requests == ["/api/tags", "/api/embed", "/api/tags"]


@pytest.mark.parametrize(
    "base_url",
    [
        "ollama:11434",
        "ftp://ollama",
        "http://user:pass@ollama:11434",
        "http://ollama:11434/v1",
        "http://ollama:11434?redirect=evil",
    ],
)
def test_rejects_unsafe_base_urls(base_url: str) -> None:
    with pytest.raises(EmbeddingConfigurationError):
        OllamaEmbeddingProvider(base_url=base_url)
