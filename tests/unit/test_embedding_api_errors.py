from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mongars.api.routes.memory import search_memory
from mongars.api.schemas import MemorySearchRequest
from mongars.config import Environment, Settings
from mongars.embeddings.errors import EmbeddingContextError
from mongars.embeddings.models import EmbeddingBatch
from mongars.embeddings.service import EmbeddingService
from mongars.memory.service import MemoryService


class _UnusedProvider:
    provider_name = "deterministic"
    model_name = "nomic-embed-text"

    async def resolve_model_digest(self) -> str:
        return "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"

    async def embed(self, *_args: object, **_kwargs: object) -> EmbeddingBatch:
        raise AssertionError("the route-level mapping test must not call the provider")

    async def aclose(self) -> None:
        return None


def test_memory_search_schema_rejects_whitespace_only_query() -> None:
    with pytest.raises(ValidationError, match="query must contain non-whitespace text"):
        MemorySearchRequest(query=" \t\n ")


@pytest.mark.asyncio
async def test_memory_search_maps_embedding_input_failure_to_bounded_422(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_search(*_args: object, **_kwargs: object) -> None:
        raise EmbeddingContextError(
            "private oversized query detail",
            provider="ollama",
            maximum_input_bytes=8_192,
            input_bytes=9_000,
            input_index=0,
        )

    monkeypatch.setattr(MemoryService, "search", fail_search)
    settings = Settings(environment=Environment.TEST)
    embeddings = EmbeddingService(
        provider=_UnusedProvider(),
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )

    with pytest.raises(HTTPException) as caught:
        await search_memory(
            request=MemorySearchRequest(query="oversized but schema-valid query"),
            principal=SimpleNamespace(subject="owner"),  # type: ignore[arg-type]
            session=object(),  # type: ignore[arg-type]
            settings=settings,
            embeddings=embeddings,
        )

    assert caught.value.status_code == 422
    assert caught.value.detail == {
        "code": "embedding_context_exceeded",
        "retryable": False,
    }
    assert "private oversized query detail" not in str(caught.value.detail)
