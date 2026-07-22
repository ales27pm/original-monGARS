from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from dataclasses import FrozenInstanceError
from datetime import UTC
from typing import Any

import httpx
import pytest

from mongars.web_search import (
    SearchResponse,
    SearxNGSearchBackend,
    WebSearchError,
    explicit_web_search_requested,
    search_query_from_request,
)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080/",
        "http://searxng:8080",
        "https://search.example.com",
    ],
)
def test_origin_accepts_https_and_explicit_local_plaintext_hosts(origin: str) -> None:
    backend = SearxNGSearchBackend(base_url=origin)
    run(backend.aclose())


@pytest.mark.parametrize(
    "origin",
    [
        "http://search.example.com",
        "http://10.0.0.5:8080",
        "https://user:password@search.example.com",
        "https://search.example.com/searxng",
        "https://search.example.com?q=term",
        "https://search.example.com#fragment",
        "ftp://search.example.com",
        "search.example.com",
        " https://search.example.com",
    ],
)
def test_origin_rejects_non_origins_and_nonlocal_plaintext(origin: str) -> None:
    with pytest.raises(ValueError):
        SearxNGSearchBackend(base_url=origin)


def test_disabled_backend_does_not_require_an_origin() -> None:
    async def exercise() -> None:
        backend = SearxNGSearchBackend(base_url=None, enabled=False)
        with pytest.raises(WebSearchError) as caught:
            await backend.search("search the web")
        assert caught.value.code == "disabled"
        assert caught.value.retryable is False
        await backend.aclose()

    run(exercise())


def test_disabled_health_is_non_blocking_without_network_access() -> None:
    async def exercise() -> None:
        def unexpected_request(_: httpx.Request) -> httpx.Response:
            raise AssertionError("disabled health must not perform a request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_request)) as client:
            backend = SearxNGSearchBackend(
                base_url=None,
                enabled=False,
                client=client,
            )
            status = await backend.health()

        assert status.enabled is False
        assert status.healthy is True
        assert status.latency_ms >= 0
        assert status.error_code is None

    run(exercise())


def test_health_probes_config_without_issuing_a_search() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/config"
            assert request.url.query == b""
            assert request.extensions["timeout"]["read"] == 3.5
            return httpx.Response(200, json={"instance_name": "monGARS Search"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                timeout=3.5,
                client=client,
            )
            status = await backend.health()

        assert status.enabled is True
        assert status.healthy is True
        assert status.latency_ms >= 0
        assert status.error_code is None

    run(exercise())


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, content=b"not-json"), "malformed_response"),
        (httpx.Response(503), "http_error"),
        (
            httpx.Response(200, headers={"content-length": "100"}, content=b"{}"),
            "response_too_large",
        ),
    ],
)
def test_health_normalizes_invalid_or_oversized_config_responses(
    response: httpx.Response,
    expected_code: str,
) -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                max_response_bytes=16,
                client=client,
            )
            status = await backend.health()

        assert status.enabled is True
        assert status.healthy is False
        assert status.latency_ms >= 0
        assert status.error_code == expected_code

    run(exercise())


def test_search_uses_get_json_parameters_timeout_and_normalizes_results() -> None:
    async def exercise() -> SearchResponse:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/search"
            assert request.url.params["q"] == "FIFA World Cup"
            assert request.url.params["format"] == "json"
            assert request.extensions["timeout"]["read"] == 3.5
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "  First\n result ",
                            "url": "HTTPS://Example.COM:443/story#section",
                            "content": "A   useful\nsummary",
                            "engine": " brave ",
                        },
                        {
                            "title": "duplicate",
                            "url": "https://example.com/story#other",
                            "content": "ignored",
                        },
                        {
                            "title": "unsafe",
                            "url": "https://user:secret@example.com/private",
                            "content": "ignored",
                        },
                        {
                            "title": "script",
                            "url": "javascript:alert(1)",
                            "content": "ignored",
                        },
                        {
                            "title": "Second",
                            "url": "http://news.example.org/item?id=2",
                            "content": "Details",
                        },
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com/",
                timeout=3.5,
                max_results=4,
                client=client,
            )
            return await backend.search("  FIFA World Cup  ")

    response = run(exercise())
    assert response.query == "FIFA World Cup"
    assert response.retrieved_at.tzinfo is UTC
    assert len(response.results) == 2
    assert response.results[0].title == "First result"
    assert response.results[0].url == "https://example.com/story"
    assert response.results[0].snippet == "A useful summary"
    assert response.results[0].engine == "brave"
    assert response.results[1].url == "http://news.example.org/item?id=2"
    with pytest.raises(FrozenInstanceError):
        response.query = "changed"  # type: ignore[misc]


def test_result_count_and_text_are_bounded() -> None:
    async def exercise() -> SearchResponse:
        payload = {
            "results": [
                {
                    "title": "x" * 400,
                    "url": f"https://example.com/{index}",
                    "content": "y" * 2_100,
                }
                for index in range(4)
            ]
        }
        transport = httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                max_results=3,
                client=client,
            )
            return await backend.search("bounded", limit=2)

    response = run(exercise())
    assert len(response.results) == 2
    assert len(response.results[0].title) == 300
    assert response.results[0].title.endswith("…")
    assert len(response.results[0].snippet) == 2_000
    assert response.results[0].snippet.endswith("…")


def test_searxng_result_order_is_preserved() -> None:
    async def exercise() -> SearchResponse:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Secondary summary",
                            "url": "https://news.example/world-cup",
                        },
                        {
                            "title": "Official FIFA report",
                            "url": "https://inside.fifa.com/news/final",
                        },
                        {
                            "title": "Another source",
                            "url": "https://sports.example/final",
                        },
                    ]
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                client=client,
            )
            return await backend.search("2026 FIFA World Cup champions", limit=2)

    response = run(exercise())
    assert [result.title for result in response.results] == [
        "Secondary summary",
        "Official FIFA report",
    ]


@pytest.mark.parametrize("query", ["", "   ", "x" * 11])
def test_query_validation_uses_stable_invalid_request_code(query: str) -> None:
    async def exercise() -> None:
        backend = SearxNGSearchBackend(
            base_url="https://search.example.com",
            max_query_chars=10,
        )
        with pytest.raises(WebSearchError) as caught:
            await backend.search(query)
        assert caught.value.code == "invalid_request"
        await backend.aclose()

    run(exercise())


def test_response_content_length_is_bounded_before_reading() -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-length": "1_000"},
                content=b"{}",
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                max_response_bytes=16,
                client=client,
            )
            with pytest.raises(WebSearchError) as caught:
                await backend.search("bounded")
        assert caught.value.code == "response_too_large"

    run(exercise())


def test_streamed_response_body_is_bounded_without_content_length() -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(200, content=b"{" + (b"x" * 32) + b"}")
        )
        async with httpx.AsyncClient(transport=transport) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                max_response_bytes=16,
                client=client,
            )
            with pytest.raises(WebSearchError) as caught:
                await backend.search("bounded")
        assert caught.value.code == "response_too_large"

    run(exercise())


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (httpx.Response(200, content=b"not-json"), "malformed_response"),
        (httpx.Response(200, json={}), "malformed_response"),
        (httpx.Response(200, json={"results": []}), "no_results"),
        (httpx.Response(503), "http_error"),
    ],
)
def test_response_failures_have_stable_codes(
    response: httpx.Response,
    expected_code: str,
) -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(lambda _: response)
        async with httpx.AsyncClient(transport=transport) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                client=client,
            )
            with pytest.raises(WebSearchError) as caught:
                await backend.search("test")
        assert caught.value.code == expected_code
        if expected_code == "http_error":
            assert caught.value.status_code == 503
            assert caught.value.retryable is True

    run(exercise())


@pytest.mark.parametrize(
    ("exception_factory", "expected_code"),
    [
        (lambda request: httpx.ReadTimeout("slow", request=request), "timeout"),
        (lambda request: httpx.ConnectError("offline", request=request), "connection_error"),
    ],
)
def test_transport_failures_have_stable_retryable_codes(
    exception_factory: Any,
    expected_code: str,
) -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise exception_factory(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                client=client,
            )
            with pytest.raises(WebSearchError) as caught:
                await backend.search("test")
        assert caught.value.code == expected_code
        assert caught.value.retryable is True

    run(exercise())


def test_aclose_does_not_close_an_injected_client() -> None:
    async def exercise() -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
        backend = SearxNGSearchBackend(
            base_url="https://search.example.com",
            client=client,
        )
        await backend.aclose()
        assert client.is_closed is False
        await client.aclose()

    run(exercise())


@pytest.mark.parametrize(
    "text",
    [
        "Search the web for the 2026 FIFA World Cup champion.",
        "Could you search online for recent reports?",
        "Browse the internet and verify this.",
        "Look this up online, please.",
        "Do a web search for current scores.",
        "Find the latest announcement on the web.",
    ],
)
def test_explicit_web_search_intent_is_detected(text: str) -> None:
    assert explicit_web_search_requested(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Search project memory for the meeting notes.",
        "Search my local documents.",
        "Find the task in the database.",
        "Summarize what you remember.",
        "",
    ],
)
def test_local_search_intent_does_not_enable_network_access(text: str) -> None:
    assert explicit_web_search_requested(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Do not search the web",
        "Never browse the internet",
        '"search the web" is a phrase',
        "Explain why someone might say search the web",
    ],
)
def test_negated_quoted_or_incidental_search_language_does_not_enable_network_access(
    text: str,
) -> None:
    assert explicit_web_search_requested(text) is False


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        (
            "Search the web for the 2026 FIFA World Cup champions.",
            "the 2026 FIFA World Cup champions.",
        ),
        ("Could you please browse the internet for current results?", "current results?"),
        ("Run a web search: official release notes", "official release notes"),
        (
            "Tell me about pgvector and search the web to verify it.",
            "Tell me about pgvector and search the web to verify it.",
        ),
    ],
)
def test_search_query_removes_only_a_leading_network_command(
    request_text: str,
    expected: str,
) -> None:
    assert search_query_from_request(request_text, max_chars=500) == expected


def test_search_query_is_bounded_after_normalization() -> None:
    assert search_query_from_request("Search the web for   abc def", max_chars=5) == "abc d"


def test_json_request_body_is_not_used() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.content == b""
            return httpx.Response(
                200,
                content=json.dumps(
                    {"results": [{"title": "one", "url": "https://example.com"}]}
                ).encode(),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend = SearxNGSearchBackend(
                base_url="https://search.example.com",
                client=client,
            )
            await backend.search("test")

    run(exercise())
