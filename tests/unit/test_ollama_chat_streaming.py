from __future__ import annotations

import json

import httpx
import pytest

from mongars.inference import (
    ChatMessage,
    InferenceResponseError,
    OllamaBackend,
)


@pytest.mark.asyncio
async def test_stream_chat_validates_payload_and_terminal_usage() -> None:
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed.update(json.loads((await request.aread()).decode("utf-8")))
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(
                b'{"model":"qwen3:4b","message":{"role":"assistant","content":"Hello "},'
                b'"done":false}\n'
                b'{"model":"qwen3:4b","message":{"role":"assistant","content":"world"},'
                b'"done":true,"done_reason":"stop","prompt_eval_count":7,"eval_count":2}\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend(
            base_url="http://ollama.test",
            chat_model="qwen3:4b",
            embedding_model="nomic-embed-text",
            think=False,
            client=client,
        )
        chunks = [
            chunk
            async for chunk in backend.stream_chat(
                (ChatMessage(role="user", content="hello"),),
                options={"temperature": 0.0},
            )
        ]

    assert observed["stream"] is True
    assert observed["think"] is False
    assert observed["options"] == {"temperature": 0.0}
    assert [chunk.content for chunk in chunks] == ["Hello ", "world"]
    assert chunks[-1].done is True
    assert chunks[-1].prompt_tokens == 7
    assert chunks[-1].completion_tokens == 2


@pytest.mark.asyncio
async def test_stream_chat_rejects_invalid_ndjson() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend(
            base_url="http://ollama.test",
            chat_model="qwen3:4b",
            embedding_model="nomic-embed-text",
            client=client,
        )
        with pytest.raises(InferenceResponseError, match="invalid NDJSON"):
            _ = [
                chunk
                async for chunk in backend.stream_chat(
                    (ChatMessage(role="user", content="hello"),)
                )
            ]


@pytest.mark.asyncio
async def test_stream_chat_requires_terminal_chunk() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"model":"qwen3:4b","message":{"role":"assistant","content":"partial"},'
                b'"done":false}\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend(
            base_url="http://ollama.test",
            chat_model="qwen3:4b",
            embedding_model="nomic-embed-text",
            client=client,
        )
        with pytest.raises(InferenceResponseError, match="terminal chunk"):
            _ = [
                chunk
                async for chunk in backend.stream_chat(
                    (ChatMessage(role="user", content="hello"),)
                )
            ]


@pytest.mark.asyncio
async def test_stream_chat_rejects_model_substitution() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"model":"other:latest","message":{"role":"assistant","content":"no"},'
                b'"done":true,"done_reason":"stop"}\n'
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = OllamaBackend(
            base_url="http://ollama.test",
            chat_model="qwen3:4b",
            embedding_model="nomic-embed-text",
            client=client,
        )
        with pytest.raises(InferenceResponseError, match="does not match"):
            _ = [
                chunk
                async for chunk in backend.stream_chat(
                    (ChatMessage(role="user", content="hello"),)
                )
            ]
