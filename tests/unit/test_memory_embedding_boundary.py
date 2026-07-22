from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from typing import Any

import pytest

from mongars.config import Environment, Settings
from mongars.embeddings import (
    EmbeddingBatch,
    EmbeddingConfigurationError,
    EmbeddingMetric,
    EmbeddingService,
)
from mongars.memory.service import MemoryService, PreparedSearch


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class RecordingEmbeddingProvider:
    provider_name = "deterministic"
    model_name = "nomic-embed-text"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        self.calls.append(tuple(texts))
        vector = tuple(0.0 for _ in range(expected_dimension))
        return EmbeddingBatch(
            embeddings=tuple(vector for _ in texts),
            model=self.model_name,
            dimension=expected_dimension,
            latency_ms=0.0,
        )

    async def aclose(self) -> None:
        return None


def memory_service(provider: RecordingEmbeddingProvider) -> MemoryService:
    settings = Settings(
        environment=Environment.TEST,
        memory_chunk_tokens=32,
        memory_chunk_overlap_tokens=0,
        embedding_batch_size=2,
    )
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
    )
    return MemoryService(settings=settings, repository=None, embeddings=embeddings)


def test_memory_search_uses_dedicated_embedding_service() -> None:
    provider = RecordingEmbeddingProvider()

    prepared = run(memory_service(provider).prepare_search("  semantic query  "))

    assert prepared.query == "semantic query"
    assert len(prepared.embedding) == 768
    assert prepared.embedding_model == "nomic-embed-text"
    assert provider.calls == [("semantic query",)]


def test_memory_boundary_rejects_model_or_character_configuration_drift() -> None:
    provider = RecordingEmbeddingProvider()
    provider.model_name = "not-reviewed"
    with pytest.raises(EmbeddingConfigurationError, match="reviewed runtime model"):
        MemoryService(
            settings=Settings(environment=Environment.TEST),
            repository=None,
            embeddings=EmbeddingService(
                provider=provider,
                expected_dimension=768,
                batch_size=2,
            ),
        )

    provider.model_name = "nomic-embed-text"
    with pytest.raises(EmbeddingConfigurationError, match="character ceiling"):
        MemoryService(
            settings=Settings(
                environment=Environment.TEST,
                memory_chunk_characters=256,
            ),
            repository=None,
            embeddings=EmbeddingService(
                provider=provider,
                expected_dimension=768,
                batch_size=2,
                max_text_characters=128,
                max_total_characters=128,
            ),
        )


def test_prepared_search_cannot_select_another_embedding_model() -> None:
    provider = RecordingEmbeddingProvider()
    memory = memory_service(provider)

    with pytest.raises(EmbeddingConfigurationError, match="unreviewed embedding model"):
        run(
            memory.search_prepared(
                owner_id="owner",
                prepared=PreparedSearch(
                    query="query",
                    embedding=tuple(0.0 for _ in range(768)),
                    embedding_model="old-model",
                ),
                top_k=1,
            )
        )


def test_memory_ingest_delegates_batching_to_embedding_service() -> None:
    provider = RecordingEmbeddingProvider()
    memory = memory_service(provider)
    prepared = memory.prepare_ingest(
        owner_id="owner-a",
        text=" ".join(f"word-{index}" for index in range(70)),
    )

    embedded = run(memory.embed_prepared_ingest(prepared))

    assert len(prepared.chunks) == 3
    assert [len(batch) for batch in provider.calls] == [2, 1]
    assert len(embedded.embeddings) == 3
    assert all(len(vector) == 768 for vector in embedded.embeddings)
    assert embedded.embedding_model == "nomic-embed-text"


def test_maximum_document_is_embedded_across_bounded_logical_requests() -> None:
    provider = RecordingEmbeddingProvider()
    metrics: list[EmbeddingMetric] = []
    settings = Settings(
        environment=Environment.TEST,
        max_document_chars=1_000,
        memory_chunk_tokens=32,
        memory_chunk_overlap_tokens=0,
        memory_chunk_characters=256,
    )
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=768,
        batch_size=2,
        max_inputs=2,
        max_text_characters=256,
        max_total_characters=300,
        metric_sink=metrics.append,
    )
    memory = MemoryService(settings=settings, repository=None, embeddings=embeddings)
    prepared = memory.prepare_ingest(owner_id="owner", text="x" * 1_000)

    result = run(memory.embed_prepared_ingest(prepared))

    assert sum(len(metric_texts) for metric_texts in provider.calls) == len(prepared.chunks)
    assert len(result.embeddings) == len(prepared.chunks)
    assert all(metric.input_count <= 2 for metric in metrics)
    assert all(len(text) <= 256 for call in provider.calls for text in call)


def test_overlap_heavy_document_is_split_before_logical_aggregate_limit() -> None:
    provider = RecordingEmbeddingProvider()
    metrics: list[EmbeddingMetric] = []
    settings = Settings(
        environment=Environment.TEST,
        max_document_chars=5_000,
        memory_chunk_tokens=32,
        memory_chunk_overlap_tokens=31,
        memory_chunk_characters=256,
    )
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=768,
        batch_size=2,
        max_inputs=3,
        max_text_characters=256,
        max_total_characters=300,
        metric_sink=metrics.append,
    )
    memory = MemoryService(settings=settings, repository=None, embeddings=embeddings)
    text = " ".join(f"w{index:03d}" for index in range(300))
    prepared = memory.prepare_ingest(owner_id="owner", text=text)

    embedded = run(memory.embed_prepared_ingest(prepared))

    assert sum(len(chunk.text) for chunk in prepared.chunks) > len(text)
    assert len(embedded.embeddings) == len(prepared.chunks)
    assert sum(metric.input_count for metric in metrics) == len(prepared.chunks)
    assert all(metric.input_count <= 3 for metric in metrics)
    assert all(sum(len(text) for text in call) <= 300 for call in provider.calls)
