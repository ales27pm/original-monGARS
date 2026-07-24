from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping, Sequence

import httpx
import pytest

from mongars.inference import (
    ChatMessage,
    ChatResponse,
    ChatStreamCompleted,
    ChatStreamDelta,
    HealthStatus,
    InferenceResponseError,
    JsonValue,
    ObservedStreamingInference,
    StreamingOllamaBackend,
)


class _StreamingBackend:
    async def chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del model, options
        raise AssertionError("the observer must use stream_chat")

    async def stream_chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamDelta | ChatStreamCompleted]:
        del options
        yield ChatStreamDelta(content="Hel")
        yield ChatStreamDelta(content="lo")
        yield ChatStreamCompleted(
            response=ChatResponse(content="Hello", model=model or "stream-model")
        )

    async def health(self) -> HealthStatus:
        return HealthStatus(
            backend="fake",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_observer_emits_deltas_and_counts_bouche_attempts() -> None:
    attempts: list[int] = []
    deltas: list[str] = []

    async def on_attempt(index: int) -> None:
        attempts.append(index)

    async def on_delta(text: str) -> None:
        deltas.append(text)

    observed = ObservedStreamingInference(
        _StreamingBackend(),
        on_attempt=on_attempt,
        on_delta=on_delta,
    )
    first = await observed.chat([ChatMessage(role="user", content="first")])
    second = await observed.chat([ChatMessage(role="user", content="second")])

    assert first.content == "Hello"
    assert second.content == "Hello"
    assert attempts == [1, 2]
    assert deltas == ["Hel", "lo", "Hel", "lo"]


@pytest.mark.asyncio
async def test_ollama_stream_suppresses_split_thinking_trace() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        frames = [
            {
                "model": "qwen-chat",
                "message": {"content": "<thi", "thinking": "separate private trace"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "nk>private"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "</think>Hel"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "lo"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "."},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        ]
        body = "".join(json.dumps(frame) + "\n" for frame in frames).encode()
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = StreamingOllamaBackend(
            base_url="http://ollama:11434",
            chat_model="qwen-chat",
            embedding_model="nomic-embed",
            think=False,
            client=client,
        )
        events = [
            event
            async for event in backend.stream_chat(
                [ChatMessage(role="user", content="hello")]
            )
        ]

    visible = "".join(
        event.content for event in events if isinstance(event, ChatStreamDelta)
    )
    completions = [
        event.response for event in events if isinstance(event, ChatStreamCompleted)
    ]
    assert visible == "Hello."
    assert completions[0].content == "Hello."
    assert completions[0].prompt_tokens == 4
    assert completions[0].completion_tokens == 2


@pytest.mark.asyncio
async def test_ollama_stream_rejects_late_thinking_marker_without_exposing_it() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        frames = [
            {
                "model": "qwen-chat",
                "message": {"content": "Answer. <thi"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "nk>secret"},
                "done": False,
            },
            {
                "model": "qwen-chat",
                "message": {"content": "</think>"},
                "done": True,
            },
        ]
        body = "".join(json.dumps(frame) + "\n" for frame in frames).encode()
        return httpx.Response(200, content=body)

    visible: list[str] = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        backend = StreamingOllamaBackend(
            base_url="http://ollama:11434",
            chat_model="qwen-chat",
            embedding_model="nomic-embed",
            client=client,
        )
        with pytest.raises(InferenceResponseError, match="thinking marker"):
            async for event in backend.stream_chat(
                [ChatMessage(role="user", content="hello")]
            ):
                if isinstance(event, ChatStreamDelta):
                    visible.append(event.content)

    assert "<think" not in "".join(visible).casefold()
