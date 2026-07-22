from __future__ import annotations

import os

import pytest

from mongars.inference import ChatMessage, OllamaBackend

pytestmark = [
    pytest.mark.inference,
    pytest.mark.skipif(
        os.getenv("MONGARS_RUN_INFERENCE_TESTS") != "1",
        reason="set MONGARS_RUN_INFERENCE_TESTS=1 to exercise real local inference",
    ),
]


@pytest.mark.asyncio
async def test_real_ollama_nonstreaming_connectivity_and_parsing() -> None:
    """Verify only live adapter connectivity and non-streaming response parsing.

    Generative wording is intentionally not a contract: even at low temperature a
    model may narrate an instruction before answering. Semantic-output behavior belongs
    in model evaluations, while this opt-in smoke test proves the production transport.
    """

    backend = OllamaBackend(
        base_url=os.getenv("MONGARS_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        chat_model=os.getenv("MONGARS_OLLAMA_CHAT_MODEL", "qwen3:4b-instruct"),
        embedding_model=os.getenv("MONGARS_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text"),
        embedding_dimension=768,
        think=False,
        timeout=float(os.getenv("MONGARS_INFERENCE_TIMEOUT_SECONDS", "180")),
        health_timeout=float(os.getenv("MONGARS_INFERENCE_HEALTH_TIMEOUT_SECONDS", "5")),
    )
    try:
        health = await backend.health()
        assert health.healthy, f"Ollama health failed with {health.error_code}"
        response = await backend.chat(
            [
                ChatMessage(
                    role="user",
                    content="Return a short acknowledgement.",
                )
            ],
            options={"temperature": 0.2, "num_predict": 16},
        )
    finally:
        await backend.aclose()

    assert response.content.strip()
    assert "<think>" not in response.content
    assert "</think>" not in response.content
    assert response.model.strip()
    assert response.prompt_tokens is not None and response.prompt_tokens > 0
    assert response.completion_tokens is not None and response.completion_tokens > 0
