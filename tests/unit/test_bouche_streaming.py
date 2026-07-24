from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from uuid import uuid4

import pytest

from mongars.autobiography.contracts import EvidenceSnapshot
from mongars.dialogue import Bouche, BoucheStreamDelta, BoucheStreamFinal, DialoguePlan
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    ChatStreamChunk,
    HealthStatus,
    InferenceResponseError,
    JsonValue,
)


class StreamingFakeInference:
    def __init__(self, chunks: Sequence[ChatStreamChunk]) -> None:
        self._chunks = tuple(chunks)
        self.chat_calls = 0

    async def chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        del options
        self.chat_calls += 1
        return ChatResponse(content="fallback [W1]", model=model or "test")

    async def stream_chat(
        self,
        _messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        del model, options
        for chunk in self._chunks:
            yield chunk

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


def plan(*, require_web: bool = False) -> DialoguePlan:
    evidence = (
        EvidenceSnapshot(
            key="W1" if require_web else "M1",
            kind="web" if require_web else "memory",
            text="Verified evidence",
            source_uri="https://example.test" if require_web else None,
        ),
    )
    return DialoguePlan(
        trace_id="trc_stream",
        session_id=uuid4(),
        messages=(
            ChatMessage(role="system", content="Follow policy."),
            ChatMessage(role="user", content="Answer."),
        ),
        model_alias="qwen3:4b",
        model_digest="a" * 64,
        options={"temperature": 0.2},
        evidence=evidence,
        estimated_prompt_tokens=100,
        context_budget=4096,
        require_web_citation=require_web,
    )


@pytest.mark.asyncio
async def test_stream_emits_deltas_then_one_validated_final() -> None:
    inference = StreamingFakeInference(
        (
            ChatStreamChunk(content="Grounded ", model="qwen3:4b"),
            ChatStreamChunk(
                content="answer [M1].",
                model="qwen3:4b",
                done=True,
                done_reason="stop",
                prompt_tokens=10,
                completion_tokens=4,
            ),
        )
    )

    events = [event async for event in Bouche(inference).stream(plan())]

    deltas = [event.text for event in events if isinstance(event, BoucheStreamDelta)]
    finals = [event.response for event in events if isinstance(event, BoucheStreamFinal)]
    assert "".join(deltas) == "Grounded answer [M1]."
    assert len(finals) == 1
    assert finals[0].answer == "Grounded answer [M1]."
    assert [citation.key for citation in finals[0].citations] == ["M1"]
    assert finals[0].prompt_tokens == 10
    assert finals[0].completion_tokens == 4


@pytest.mark.asyncio
async def test_stream_deltas_exactly_match_the_normalized_final_answer() -> None:
    inference = StreamingFakeInference(
        (
            ChatStreamChunk(content=" \n  Grounded", model="qwen3:4b"),
            ChatStreamChunk(content=" answer [M1].   ", model="qwen3:4b", done=True),
        )
    )

    events = [event async for event in Bouche(inference).stream(plan())]
    delta_text = "".join(
        event.text for event in events if isinstance(event, BoucheStreamDelta)
    )
    final = next(event.response for event in events if isinstance(event, BoucheStreamFinal))

    assert delta_text == "Grounded answer [M1]."
    assert final.answer == delta_text


@pytest.mark.asyncio
async def test_stream_withholds_split_hidden_reasoning_marker() -> None:
    inference = StreamingFakeInference(
        (
            ChatStreamChunk(content="<thi", model="qwen3:4b"),
            ChatStreamChunk(content="nk>secret", model="qwen3:4b"),
            ChatStreamChunk(content="</think>Visible", model="qwen3:4b", done=True),
        )
    )
    emitted: list[object] = []

    with pytest.raises(InferenceResponseError, match="hidden-reasoning marker"):
        async for event in Bouche(inference).stream(plan()):
            emitted.append(event)

    assert emitted == []


@pytest.mark.asyncio
async def test_required_web_citation_uses_validated_fallback_before_streaming() -> None:
    inference = StreamingFakeInference(())

    events = [event async for event in Bouche(inference).stream(plan(require_web=True))]

    assert inference.chat_calls == 1
    assert "".join(
        event.text for event in events if isinstance(event, BoucheStreamDelta)
    ) == "fallback [W1]"
    final = next(event.response for event in events if isinstance(event, BoucheStreamFinal))
    assert final.answer == "fallback [W1]"
    assert [citation.key for citation in final.citations] == ["W1"]
