from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping, Sequence
from typing import Any
from uuid import uuid4

import pytest

from mongars.autobiography.contracts import EvidenceSnapshot
from mongars.dialogue import Bouche, DialoguePlan
from mongars.inference.base import ChatMessage, ChatResponse, HealthStatus, JsonValue
from mongars.inference.base import InferenceResponseError


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class FakeInference:
    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = iter(responses)
        self.calls: list[tuple[ChatMessage, ...]] = []

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        self.calls.append(tuple(messages))
        return ChatResponse(
            content=next(self._responses),
            model=model or "test-model",
            done_reason="stop",
            prompt_tokens=10,
            completion_tokens=4,
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


def plan(*, require_web: bool = False) -> DialoguePlan:
    return DialoguePlan(
        trace_id="trc_test",
        session_id=uuid4(),
        messages=(
            ChatMessage(role="system", content="Follow policy."),
            ChatMessage(role="tool", content='{"kind":"web_search_results"}'),
            ChatMessage(role="user", content="What happened?"),
        ),
        model_alias="qwen3:4b-instruct",
        model_digest="a" * 64,
        options={"temperature": 0.0},
        evidence=(
            EvidenceSnapshot(
                key="W1",
                kind="web",
                text="Verified result",
                title="Official result",
                source_uri="https://example.test/result",
                rank=0,
            ),
        ),
        estimated_prompt_tokens=100,
        context_budget=4096,
        require_web_citation=require_web,
    )


def test_returns_validated_citation_binding() -> None:
    inference = FakeInference(["The official result is confirmed [W1]."])

    result = run(Bouche(inference).compose(plan()))

    assert result.answer.endswith("[W1].")
    assert result.grounding_status == "grounded"
    assert [binding.key for binding in result.citations] == ["W1"]
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 4


def test_unknown_citation_is_rejected() -> None:
    inference = FakeInference(["Unsupported claim [W2]."])

    with pytest.raises(InferenceResponseError, match="unknown evidence key W2"):
        run(Bouche(inference).compose(plan()))


def test_required_web_citation_gets_one_corrective_retry() -> None:
    inference = FakeInference(["No citation yet.", "Corrected answer [W1]."])

    result = run(Bouche(inference).compose(plan(require_web=True)))

    assert result.answer == "Corrected answer [W1]."
    assert len(inference.calls) == 2
    assert inference.calls[1][-2].role == "tool"
    assert "allowed_web_keys" in inference.calls[1][-2].content


def test_required_web_citation_fails_after_retry() -> None:
    inference = FakeInference(["No citation.", "Still no citation."])

    with pytest.raises(InferenceResponseError, match="did not cite required web evidence"):
        run(Bouche(inference).compose(plan(require_web=True)))


def test_hidden_reasoning_marker_is_rejected() -> None:
    inference = FakeInference(["<think>secret</think>Visible"])

    with pytest.raises(InferenceResponseError, match="hidden-reasoning marker"):
        run(Bouche(inference).compose(plan()))
