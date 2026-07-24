from __future__ import annotations

import json
from uuid import uuid4

import pytest

from mongars.config import Environment, Settings
from mongars.dialogue import DialoguePlan
from mongars.inference.base import ChatMessage
from mongars.memory.repository import MemoryHit
from mongars.orchestrator.typed_evidence import (
    canonical_prompt_bytes,
    key_prompt_evidence,
    reserve_evidence_key_budget,
)


def test_memory_key_preserves_locator_and_exact_prompt_text() -> None:
    hit = MemoryHit(
        chunk_id=uuid4(),
        document_id=uuid4(),
        score=0.91,
        text="Grounded document text.",
        source_uri="file:///manual.pdf",
        title="Manual",
        locator={"kind": "pdf_page", "page": 7},
    )
    message = ChatMessage(
        role="tool",
        content=json.dumps(
            {
                "kind": "retrieved_memory",
                "untrusted": True,
                "results": [
                    {
                        "chunk_id": str(hit.chunk_id),
                        "title": hit.title,
                        "source_uri": hit.source_uri,
                        "locator": hit.locator,
                        "text": hit.text,
                    }
                ],
            }
        ),
    )

    keyed = key_prompt_evidence(
        messages=(message, ChatMessage(role="user", content="Question")),
        included_history=(),
        included_hits=(hit,),
        included_web_results=(),
        history_source_ids={},
        web_retrieved_at=None,
        context_budget=4096,
    )

    payload = json.loads(keyed.messages[0].content)
    assert payload["results"][0]["key"] == "M1"
    assert keyed.evidence[0].key == "M1"
    assert keyed.evidence[0].source_id == str(hit.chunk_id)
    assert keyed.evidence[0].locator == {"kind": "pdf_page", "page": 7}
    assert keyed.evidence[0].score == 0.91


def test_evidence_key_overhead_fails_closed_when_budget_is_exhausted() -> None:
    message = ChatMessage(
        role="tool",
        content=json.dumps(
            {
                "kind": "conversation_history",
                "messages": [{"role": "user", "content": "prior"}],
            }
        ),
    )

    with pytest.raises(ValueError, match="evidence identifiers exceed"):
        key_prompt_evidence(
            messages=(message, ChatMessage(role="user", content="Question")),
            included_history=(),
            included_hits=(),
            included_web_results=(),
            history_source_ids={},
            web_retrieved_at=None,
            context_budget=1,
        )


def test_budget_reservation_is_noop_without_evidence_candidates() -> None:
    settings = Settings(environment=Environment.TEST)

    assert reserve_evidence_key_budget(settings, candidate_count=0) is settings
    reserved = reserve_evidence_key_budget(settings, candidate_count=3)
    assert reserved.ollama_num_predict > settings.ollama_num_predict
    assert reserved.ollama_context_length == settings.ollama_context_length


def test_canonical_prompt_identity_is_stable_and_includes_policy_metadata() -> None:
    plan = DialoguePlan(
        trace_id="trc_test",
        session_id=uuid4(),
        messages=(
            ChatMessage(role="system", content="Policy"),
            ChatMessage(role="user", content="Question"),
        ),
        model_alias="qwen3:4b-instruct",
        model_digest="a" * 64,
        options={"temperature": 0.2, "stop": ["END"]},
        evidence=(),
        estimated_prompt_tokens=50,
        context_budget=4096,
        response_mode="answer",
        require_web_citation=False,
        prompt_recipe_version="bouche-v1",
        policy_version="cortex-v1",
    )

    first = canonical_prompt_bytes(plan)
    second = canonical_prompt_bytes(plan)
    payload = json.loads(first)

    assert first == second
    assert payload["model_digest"] == "a" * 64
    assert payload["prompt_recipe_version"] == "bouche-v1"
    assert payload["policy_version"] == "cortex-v1"
    assert payload["options"]["stop"] == ["END"]
