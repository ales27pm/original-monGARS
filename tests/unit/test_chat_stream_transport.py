from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from mongars.api.chat_streaming import _stream_frames
from mongars.api.schemas import ChatRequest
from mongars.config import Environment, Settings
from mongars.inference.base import (
    ChatMessage,
    ChatResponse,
    ChatStreamCompleted,
    ChatStreamDelta,
    HealthStatus,
    JsonValue,
)
from mongars.orchestrator.typed_chat import TypedChatResult


class _StreamingBackend:
    def __init__(self) -> None:
        self.answers = iter(("draft", "corrected"))

    async def chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del model, options
        raise AssertionError("the observer must consume stream_chat")

    async def stream_chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamDelta | ChatStreamCompleted]:
        del options
        answer = next(self.answers)
        yield ChatStreamDelta(content=answer)
        yield ChatStreamCompleted(
            response=ChatResponse(content=answer, model=model or "stream-model")
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
async def test_transport_resets_provisional_text_for_a_second_bouche_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Runtime:
        def __init__(self, *, inference: Any, **_kwargs: Any) -> None:
            self.inference = inference

        async def chat(self, **_kwargs: Any) -> TypedChatResult:
            messages = [ChatMessage(role="user", content="hello")]
            await self.inference.chat(messages, model="model")
            corrected = await self.inference.chat(messages, model="model")
            return TypedChatResult(
                trace_id="trc_test",
                session_id=uuid4(),
                answer=corrected.content,
                model=corrected.model,
                memory_hits=0,
                web_search_status="not_requested",
                sources=(),
                citations=(),
            )

    monkeypatch.setattr("mongars.api.chat_streaming.TypedChatRuntime", _Runtime)
    frames = [
        json.loads(frame)
        async for frame in _stream_frames(
            request=ChatRequest(message="hello"),
            owner_id="owner",
            session=SimpleNamespace(),  # type: ignore[arg-type]
            settings=Settings(environment=Environment.TEST),
            inference=_StreamingBackend(),
            embeddings=SimpleNamespace(),  # type: ignore[arg-type]
            personality=None,
            web_search=None,
        )
    ]

    assert [frame["type"] for frame in frames] == [
        "start",
        "attempt",
        "delta",
        "reset",
        "attempt",
        "delta",
        "final",
    ]
    assert frames[2]["text"] == "draft"
    assert frames[5]["text"] == "corrected"
    assert frames[6]["response"]["answer"] == "corrected"


@pytest.mark.asyncio
async def test_closing_transport_cancels_the_runtime_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class _Runtime:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        async def chat(self, **_kwargs: Any) -> TypedChatResult:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            raise AssertionError("unreachable")

    monkeypatch.setattr("mongars.api.chat_streaming.TypedChatRuntime", _Runtime)
    stream = _stream_frames(
        request=ChatRequest(message="hello"),
        owner_id="owner",
        session=SimpleNamespace(),  # type: ignore[arg-type]
        settings=Settings(environment=Environment.TEST),
        inference=_StreamingBackend(),
        embeddings=SimpleNamespace(),  # type: ignore[arg-type]
        personality=None,
        web_search=None,
    )
    first = json.loads(await anext(stream))
    assert first["type"] == "start"
    await started.wait()
    await stream.aclose()
    assert cancelled.is_set()
