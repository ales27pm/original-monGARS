from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from mongars.api.chat_streaming import ChatStreamPump
from mongars.autobiography.contracts import EvidenceSnapshot
from mongars.dialogue import CitationBinding, DialoguePlan
from mongars.inference import ChatMessage, InferenceResponseError
from mongars.orchestrator.cortex import ChatResult
from mongars.orchestrator.typed_chat import TypedChatResult


def _plan() -> DialoguePlan:
    return DialoguePlan(
        trace_id="trc_transport",
        session_id=uuid4(),
        messages=(ChatMessage(role="user", content="hello"),),
        model_alias="qwen3:4b",
        model_digest="a" * 64,
        options={},
        evidence=(
            EvidenceSnapshot(
                key="M1",
                kind="memory",
                text="evidence",
                title="Manual",
                locator={"page": 7},
            ),
        ),
        estimated_prompt_tokens=20,
        context_budget=4096,
    )


@pytest.mark.asyncio
async def test_stream_pump_emits_start_sources_delta_and_final() -> None:
    plan = _plan()
    result = TypedChatResult(
        trace_id=plan.trace_id,
        session_id=plan.session_id,
        answer="answer [M1]",
        model="qwen3:4b",
        memory_hits=1,
        web_search_status="not_requested",
        sources=(),
        citations=(
            CitationBinding(
                key="M1",
                kind="memory",
                source_id=None,
                title="Manual",
                source_uri=None,
                locator={"page": 7},
            ),
        ),
    )
    pump = ChatStreamPump()

    await pump.on_start(plan)
    await pump.on_delta("answer [M1]")
    await pump.finish(result)
    await pump.close()
    frames = [json.loads(item) async for item in _decoded(pump)]

    assert [frame["type"] for frame in frames] == ["start", "sources", "delta", "final"]
    assert frames[0]["trace_id"] == plan.trace_id
    assert frames[1]["sources"][0]["key"] == "M1"
    assert frames[1]["sources"][0]["locator"] == {"page": 7}
    assert frames[-1]["citations"][0]["key"] == "M1"
    assert frames[-1]["answer"] == "answer [M1]"


@pytest.mark.asyncio
async def test_stream_pump_rejects_a_final_answer_that_differs_from_queued_deltas() -> None:
    plan = _plan()
    result = TypedChatResult(
        trace_id=plan.trace_id,
        session_id=plan.session_id,
        answer="substituted final",
        model="qwen3:4b",
        memory_hits=0,
        web_search_status="not_requested",
        sources=(),
        citations=(),
    )
    pump = ChatStreamPump()

    await pump.on_start(plan)
    await pump.on_delta("displayed draft")

    with pytest.raises(InferenceResponseError, match="do not match"):
        await pump.finish(result)


@pytest.mark.asyncio
async def test_stream_pump_supports_the_lightweight_cortex_result() -> None:
    result = ChatResult(
        trace_id="trc_legacy",
        session_id=uuid4(),
        answer="legacy bounded answer",
        model="deterministic-chat",
        memory_hits=0,
        web_search_status="not_requested",
        sources=(),
    )
    pump = ChatStreamPump()

    await pump.finish(result)
    await pump.close()
    frames = [json.loads(item) async for item in _decoded(pump)]

    assert [frame["type"] for frame in frames] == ["start", "sources", "delta", "final"]
    assert frames[-1]["answer"] == result.answer
    assert frames[-1]["citations"] == []


@pytest.mark.asyncio
async def test_stream_pump_emits_bounded_public_error() -> None:
    class PrivateFailure(RuntimeError):
        code = "inference_timeout"
        retryable = True

    pump = ChatStreamPump()
    await pump.fail(PrivateFailure("private backend address and prompt"))
    await pump.close()
    frames = [json.loads(item) async for item in _decoded(pump)]

    assert frames == [
        {"type": "error", "code": "inference_timeout", "retryable": True}
    ]
    assert "private" not in json.dumps(frames)


@pytest.mark.asyncio
async def test_stream_pump_applies_backpressure_when_the_queue_is_full() -> None:
    pump = ChatStreamPump()
    await pump.on_start(_plan())
    for _ in range(62):
        await pump.on_delta("x")

    blocked = asyncio.create_task(pump.on_delta("y"))
    await asyncio.sleep(0)
    assert blocked.done() is False

    stream = pump.bytes()
    first = await anext(stream)
    assert json.loads(first)["type"] == "start"
    await asyncio.wait_for(blocked, timeout=1)
    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_pump_rejects_an_oversized_accumulated_answer() -> None:
    pump = ChatStreamPump()
    await pump.on_start(_plan())

    with pytest.raises(InferenceResponseError, match="answer-size ceiling"):
        await pump.on_delta("x" * 1_000_001)


async def _decoded(pump: ChatStreamPump):
    async for item in pump.bytes():
        yield item.decode("utf-8")
