from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest

from mongars.config import Environment, Settings
from mongars.embeddings import (
    EmbeddingBatch,
    EmbeddingConfigurationError,
    EmbeddingContextError,
    EmbeddingMetric,
    EmbeddingService,
)
from mongars.memory.chunking import TextChunk
from mongars.memory.repository import EmbeddingInventory, ReindexChunk
from mongars.memory.service import MemoryService, PreparedSearch


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class RecordingEmbeddingProvider:
    provider_name = "deterministic"
    model_name = "nomic-embed-text"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def resolve_model_digest(self) -> str:
        return "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f"

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        self.calls.append(tuple(texts))
        vector = tuple(1.0 if index == 0 else 0.0 for index in range(expected_dimension))
        return EmbeddingBatch(
            embeddings=tuple(vector for _ in texts),
            model=self.model_name,
            model_digest=await self.resolve_model_digest(),
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
    assert prepared.embedding_space.model_alias == "nomic-embed-text"
    assert provider.calls == [("search_query: semantic query",)]


def test_prepared_search_matches_provider_unicode_and_newline_normalization() -> None:
    provider = RecordingEmbeddingProvider()

    prepared = run(memory_service(provider).prepare_search("  cafe\u0301\r\nline  "))

    assert prepared.query == "café\nline"
    assert provider.calls == [("search_query: café\nline",)]


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

    prepared = run(memory.prepare_search("query"))
    invalid_space = replace(prepared.embedding_space, model_alias="old-model")
    with pytest.raises(EmbeddingConfigurationError, match="unreviewed embedding space"):
        run(
            memory.search_prepared(
                owner_id="owner",
                prepared=PreparedSearch(
                    query="query",
                    embedding=tuple(0.0 for _ in range(768)),
                    embedding_space=invalid_space,
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
    assert embedded.embedding_space.model_alias == "nomic-embed-text"


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
    assert all(
        len(text.removeprefix("search_document: ")) <= 256
        for call in provider.calls
        for text in call
    )


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
    assert all(
        sum(len(text.removeprefix("search_document: ")) for text in call) <= 300
        for call in provider.calls
    )


def test_reindex_splits_multibyte_legacy_chunk_and_preserves_locator() -> None:
    provider = RecordingEmbeddingProvider()
    settings = Settings(
        environment=Environment.TEST,
        memory_chunk_characters=256,
    )
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=768,
        batch_size=8,
        max_text_characters=256,
        max_total_characters=1_000,
        max_text_bytes=64,
        max_total_bytes=256,
    )
    memory = MemoryService(settings=settings, repository=None, embeddings=embeddings)
    source = ReindexChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        chunk=TextChunk(
            text="界" * 100,
            approximate_tokens=100,
            section_path=("Legacy",),
            locator={"page": 7, "heading_path": ["Legacy"]},
        ),
    )

    embedded = run(memory.embed_reindex_chunks([source]))

    replacement = embedded.replacements[0]
    assert len(replacement.chunks) > 1
    assert "".join(chunk.text for chunk in replacement.chunks) == source.chunk.text
    assert all(chunk.locator == source.chunk.locator for chunk in replacement.chunks)
    assert all(chunk.section_path == source.chunk.section_path for chunk in replacement.chunks)
    assert all(len(text.encode("utf-8")) <= 64 for call in provider.calls for text in call)


def test_embedding_batches_respect_prepared_utf8_aggregate_bytes() -> None:
    provider = RecordingEmbeddingProvider()
    settings = Settings(
        environment=Environment.TEST,
        memory_chunk_characters=256,
    )
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=768,
        batch_size=8,
        max_text_characters=256,
        max_total_characters=512,
        max_text_bytes=80,
        max_total_bytes=100,
    )
    memory = MemoryService(settings=settings, repository=None, embeddings=embeddings)
    chunks = [
        ReindexChunk(
            chunk_id=uuid4(),
            document_id=uuid4(),
            chunk=TextChunk(text="é" * 20, approximate_tokens=20),
        )
        for _index in range(2)
    ]

    embedded = run(memory.embed_reindex_chunks(chunks))

    assert len(embedded.replacements) == 2
    assert [len(call) for call in provider.calls] == [1, 1]
    assert all(sum(len(text.encode("utf-8")) for text in call) <= 100 for call in provider.calls)


def test_search_fails_closed_when_owner_has_uncovered_chunks() -> None:
    provider = RecordingEmbeddingProvider()
    memory = memory_service(provider)
    prepared = run(memory.prepare_search("query"))

    class IncompleteRepository:
        async def embedding_inventory(self, **_kwargs: object) -> EmbeddingInventory:
            return EmbeddingInventory(compatible_chunk_count=1, legacy_chunk_count=1)

        async def search(self, **_kwargs: object) -> list[object]:
            raise AssertionError("partial memory must never be searched")

    memory._repository = IncompleteRepository()  # type: ignore[assignment]

    with pytest.raises(EmbeddingConfigurationError, match="approved embedding reindex"):
        run(memory.search_prepared(owner_id="owner", prepared=prepared, top_k=1))


def test_exact_embedding_space_policy_is_required() -> None:
    provider = RecordingEmbeddingProvider()
    memory = memory_service(provider)
    prepared = run(memory.prepare_search("query"))
    drifted = replace(prepared.embedding_space, query_instruction="query: ")

    with pytest.raises(EmbeddingConfigurationError, match="unreviewed embedding space"):
        run(
            memory.search_prepared(
                owner_id="owner",
                prepared=replace(prepared, embedding_space=drifted),
                top_k=1,
            )
        )


def test_search_rejects_prepared_multibyte_input_over_context() -> None:
    provider = RecordingEmbeddingProvider()
    settings = Settings(environment=Environment.TEST, memory_chunk_characters=256)
    embeddings = EmbeddingService(
        provider=provider,
        expected_dimension=768,
        batch_size=2,
        max_text_characters=256,
        max_total_characters=256,
        max_text_bytes=40,
        max_total_bytes=40,
    )
    memory = MemoryService(settings=settings, repository=None, embeddings=embeddings)

    with pytest.raises(EmbeddingContextError) as exc_info:
        run(memory.prepare_search("界" * 10))

    assert exc_info.value.maximum_input_bytes == 40
    assert exc_info.value.input_bytes is not None
    assert exc_info.value.input_bytes > 40
    assert provider.calls == []


def test_note_identity_uses_canonical_unicode_and_newlines() -> None:
    memory = memory_service(RecordingEmbeddingProvider())

    decomposed = memory.prepare_ingest(
        owner_id="owner",
        text="  cafe\u0301\r\nsecond line  ",
    )
    canonical = memory.prepare_ingest(
        owner_id="owner",
        text="café\nsecond line",
    )

    assert decomposed.source_sha256 == canonical.source_sha256
    assert [chunk.text for chunk in decomposed.chunks] == [chunk.text for chunk in canonical.chunks]
    assert decomposed.chunks[0].text == "café second line"


def test_explicit_raw_source_identity_separates_equal_extracted_text() -> None:
    memory = memory_service(RecordingEmbeddingProvider())
    first_digest = bytes.fromhex("11" * 32)
    second_digest = bytes.fromhex("22" * 32)

    first = memory.prepare_ingest(
        owner_id="owner",
        text="identical extracted text",
        source_type="document",
        source_sha256=first_digest,
    )
    second = memory.prepare_ingest(
        owner_id="owner",
        text="identical extracted text",
        source_type="document",
        source_sha256=second_digest,
    )

    assert first.source_sha256 == first_digest
    assert second.source_sha256 == second_digest
    assert first.source_sha256 != second.source_sha256
    assert first.chunks == second.chunks


@pytest.mark.parametrize("invalid_digest", [b"", b"x" * 31, b"x" * 33, bytearray(32)])
def test_explicit_source_identity_requires_exact_immutable_sha256(
    invalid_digest: object,
) -> None:
    memory = memory_service(RecordingEmbeddingProvider())

    with pytest.raises(ValueError, match="exactly 32 immutable bytes"):
        memory.prepare_ingest(
            owner_id="owner",
            text="content",
            source_sha256=invalid_digest,  # type: ignore[arg-type]
        )
