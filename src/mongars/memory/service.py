from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mongars.config import Settings
from mongars.db.models import MemoryDocument
from mongars.embeddings.errors import EmbeddingConfigurationError, EmbeddingContextError
from mongars.embeddings.models import EmbeddingSpace
from mongars.embeddings.service import EmbeddingService
from mongars.ingestion.chunking import chunk_segments
from mongars.ingestion.models import ExtractedSegment
from mongars.memory.chunking import TextChunk, chunk_text
from mongars.memory.repository import (
    MemoryHit,
    MemoryRepository,
    ReindexChunk,
    ReindexReplacement,
    validate_duplicate_governance,
)


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: MemoryDocument
    created: bool
    chunk_count: int


@dataclass(frozen=True, slots=True)
class PreparedIngest:
    owner_id: str
    source_sha256: bytes
    source_type: str
    title: str | None
    source_uri: str | None
    mime_type: str | None
    sensitivity: str
    retention_class: str
    expires_at: datetime | None
    metadata: dict[str, Any]
    chunks: tuple[TextChunk, ...]


@dataclass(frozen=True, slots=True)
class EmbeddedIngest:
    prepared: PreparedIngest
    embeddings: tuple[tuple[float, ...], ...]
    embedding_space: EmbeddingSpace


@dataclass(frozen=True, slots=True)
class PreparedSearch:
    query: str
    embedding: tuple[float, ...]
    embedding_space: EmbeddingSpace


@dataclass(frozen=True, slots=True)
class EmbeddedReindex:
    replacements: tuple[ReindexReplacement, ...]
    embedding_space: EmbeddingSpace


class MemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: MemoryRepository | None,
        embeddings: EmbeddingService,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._embeddings = embeddings
        if embeddings.model_name != settings.ollama_embedding_model:
            raise EmbeddingConfigurationError(
                "MemoryService embedding model does not match the reviewed runtime model.",
                provider=embeddings.provider_name,
            )
        if embeddings.dimension != settings.embedding_dimensions:
            raise EmbeddingConfigurationError(
                "MemoryService embedding dimension does not match the database schema.",
                provider=embeddings.provider_name,
            )
        if settings.memory_chunk_characters > embeddings.max_text_characters:
            raise EmbeddingConfigurationError(
                "Memory chunk character ceiling exceeds the embedding service boundary.",
                provider=embeddings.provider_name,
            )

    def prepare_ingest(
        self,
        *,
        owner_id: str,
        text: str,
        source_type: str = "note",
        title: str | None = None,
        source_uri: str | None = None,
        mime_type: str | None = "text/plain",
        sensitivity: str = "private",
        retention_class: str = "keep",
        metadata: dict[str, Any] | None = None,
        segments: tuple[ExtractedSegment, ...] | None = None,
        source_sha256: bytes | None = None,
    ) -> PreparedIngest:
        normalized = _normalize_memory_text(text)
        if not normalized:
            raise ValueError("document text must not be empty")
        if len(normalized) > self._settings.max_document_chars:
            raise ValueError("document exceeds the configured character limit")
        if source_sha256 is not None and (
            not isinstance(source_sha256, bytes) or len(source_sha256) != 32
        ):
            raise ValueError("source_sha256 must be exactly 32 immutable bytes")

        if segments is None:
            raw_chunks = chunk_text(
                normalized,
                max_tokens=self._settings.memory_chunk_tokens,
                overlap_tokens=self._settings.memory_chunk_overlap_tokens,
                max_characters=self._settings.memory_chunk_characters,
            )
        else:
            if not segments:
                raise ValueError("structured document must contain at least one segment")
            normalized_segments = tuple(
                ExtractedSegment(
                    text=_normalize_memory_text(segment.text),
                    locator=segment.locator,
                )
                for segment in segments
            )
            if any(not segment.text for segment in normalized_segments):
                raise ValueError("structured document segments must not be empty")
            projected = "\n\n".join(segment.text for segment in normalized_segments)
            if projected != normalized:
                raise ValueError("structured segments do not match the normalized document text")
            raw_chunks = [
                TextChunk(
                    text=chunk.text,
                    approximate_tokens=chunk.approximate_tokens,
                    section_path=chunk.locator.heading_path,
                    locator=chunk.locator.as_dict(),
                )
                for chunk in chunk_segments(
                    normalized_segments,
                    max_tokens=self._settings.memory_chunk_tokens,
                    overlap_tokens=self._settings.memory_chunk_overlap_tokens,
                    max_characters=self._settings.memory_chunk_characters,
                )
            ]
        chunks = _split_chunks_for_embedding_bytes(
            raw_chunks,
            maximum_bytes=self._embedding_document_payload_bytes,
            maximum_characters=self._embeddings.max_text_characters,
        )
        return PreparedIngest(
            owner_id=owner_id,
            source_sha256=(
                source_sha256
                if source_sha256 is not None
                else hashlib.sha256(normalized.encode("utf-8")).digest()
            ),
            source_type=source_type,
            title=title,
            source_uri=source_uri,
            mime_type=mime_type,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=_expiry_for_retention(retention_class),
            metadata=dict(metadata or {}),
            chunks=tuple(chunks),
        )

    async def resolve_existing_ingest(
        self,
        prepared: PreparedIngest,
        *,
        embedding_space: EmbeddingSpace,
    ) -> IngestResult | None:
        """Resolve a duplicate in a short transaction before external embedding.

        Equal content is only idempotent when sensitivity and retention match. A
        same-retention TTL submission preserves the original expiry rather than
        extending it. Every distinct source observation is recorded separately.
        """

        self._validate_embedding_space(embedding_space)
        repository = self._require_repository()
        existing = await repository.find_by_digest(
            owner_id=prepared.owner_id,
            digest=prepared.source_sha256,
        )
        if existing is None:
            return None
        await repository.require_document_embedding_space(
            document_id=existing.id,
            embedding_space_id=embedding_space.space_id,
        )
        validate_duplicate_governance(
            existing,
            sensitivity=prepared.sensitivity,
            retention_class=prepared.retention_class,
        )
        await repository.add_provenance(
            document_id=existing.id,
            source_type=prepared.source_type,
            source_uri=prepared.source_uri,
            title=prepared.title,
            mime_type=prepared.mime_type,
            metadata=prepared.metadata,
        )
        return IngestResult(document=existing, created=False, chunk_count=0)

    async def embed_prepared_ingest(self, prepared: PreparedIngest) -> EmbeddedIngest:
        """Call the embedding backend without touching the database repository."""

        vectors: list[tuple[float, ...]] = []
        embedded_chunks: list[TextChunk] = []
        space = await self._embeddings.resolve_space()
        self._validate_embedding_space(space)
        pending: list[TextChunk] = []
        pending_characters = 0
        pending_bytes = 0
        instruction_bytes = len(
            self._embeddings.profile.instruction_for("search_document").encode("utf-8")
        )

        async def flush() -> None:
            nonlocal pending, pending_characters, pending_bytes
            if not pending:
                return
            try:
                response = await self._embeddings.embed(
                    [chunk.text for chunk in pending],
                    purpose="search_document",
                )
            except EmbeddingContextError:
                for chunk in pending:
                    chunk_parts, chunk_vectors = await self._embed_chunk_recursively(chunk)
                    embedded_chunks.extend(chunk_parts)
                    vectors.extend(chunk_vectors)
            else:
                if response.embedding_space_id != space.space_id:
                    raise EmbeddingConfigurationError(
                        "Embedding space changed while processing one document.",
                        provider=self._embeddings.provider_name,
                    )
                embedded_chunks.extend(pending)
                vectors.extend(response.embeddings)
            pending = []
            pending_characters = 0
            pending_bytes = 0

        for chunk in prepared.chunks:
            chunk_characters = len(chunk.text)
            chunk_bytes = len(chunk.text.encode("utf-8")) + instruction_bytes
            if chunk_characters > self._embeddings.max_text_characters:
                raise EmbeddingConfigurationError(
                    "A memory chunk exceeds the embedding service text boundary.",
                    provider=self._embeddings.provider_name,
                )
            if pending and (
                len(pending) >= self._embeddings.max_inputs
                or pending_characters + chunk_characters > self._embeddings.max_total_characters
                or pending_bytes + chunk_bytes > self._embeddings.max_total_bytes
            ):
                await flush()
            pending.append(chunk)
            pending_characters += chunk_characters
            pending_bytes += chunk_bytes
        await flush()
        if not embedded_chunks:
            raise EmbeddingConfigurationError(
                "Prepared ingestion produced no embeddable chunks.",
                provider=self._embeddings.provider_name,
            )
        return EmbeddedIngest(
            prepared=PreparedIngest(
                owner_id=prepared.owner_id,
                source_sha256=prepared.source_sha256,
                source_type=prepared.source_type,
                title=prepared.title,
                source_uri=prepared.source_uri,
                mime_type=prepared.mime_type,
                sensitivity=prepared.sensitivity,
                retention_class=prepared.retention_class,
                expires_at=prepared.expires_at,
                metadata=prepared.metadata,
                chunks=tuple(embedded_chunks),
            ),
            embeddings=tuple(vectors),
            embedding_space=space,
        )

    async def embed_reindex_chunks(
        self,
        chunks: list[ReindexChunk],
    ) -> EmbeddedReindex:
        """Split and embed legacy chunks without opening a persistence transaction."""

        space = await self._embeddings.resolve_space()
        self._validate_embedding_space(space)
        if not chunks:
            return EmbeddedReindex(replacements=(), embedding_space=space)

        prepared_by_source: dict[UUID, list[TextChunk]] = {}
        for source in chunks:
            if source.chunk_id in prepared_by_source:
                raise ValueError("reindex source chunk IDs must be unique")
            prepared_by_source[source.chunk_id] = _split_chunks_for_embedding_bytes(
                [source.chunk],
                maximum_bytes=self._embedding_document_payload_bytes,
                maximum_characters=self._embeddings.max_text_characters,
            )

        embedded_by_source: dict[UUID, list[tuple[TextChunk, tuple[float, ...]]]] = {
            source.chunk_id: [] for source in chunks
        }
        pending: list[tuple[UUID, TextChunk]] = []
        pending_characters = 0
        pending_bytes = 0
        instruction_bytes = len(
            self._embeddings.profile.instruction_for("search_document").encode("utf-8")
        )

        async def flush() -> None:
            nonlocal pending, pending_characters, pending_bytes
            if not pending:
                return
            try:
                response = await self._embeddings.embed(
                    [part.text for _source_id, part in pending],
                    purpose="search_document",
                )
            except EmbeddingContextError:
                for source_id, part in pending:
                    nested_parts, nested_vectors = await self._embed_chunk_recursively(part)
                    embedded_by_source[source_id].extend(
                        zip(nested_parts, nested_vectors, strict=True)
                    )
            else:
                if response.embedding_space_id != space.space_id:
                    raise EmbeddingConfigurationError(
                        "Embedding space changed during memory reindex.",
                        provider=self._embeddings.provider_name,
                    )
                for (source_id, part), vector in zip(
                    pending,
                    response.embeddings,
                    strict=True,
                ):
                    embedded_by_source[source_id].append((part, vector))
            pending = []
            pending_characters = 0
            pending_bytes = 0

        for source in chunks:
            for part in prepared_by_source[source.chunk_id]:
                part_characters = len(part.text)
                part_bytes = len(part.text.encode("utf-8")) + instruction_bytes
                if pending and (
                    len(pending) >= self._embeddings.max_inputs
                    or pending_characters + part_characters > self._embeddings.max_total_characters
                    or pending_bytes + part_bytes > self._embeddings.max_total_bytes
                ):
                    await flush()
                pending.append((source.chunk_id, part))
                pending_characters += part_characters
                pending_bytes += part_bytes
        await flush()

        replacements: list[ReindexReplacement] = []
        for source in chunks:
            embedded = embedded_by_source[source.chunk_id]
            if not embedded:
                raise EmbeddingConfigurationError(
                    "Memory reindex produced no embeddable chunks.",
                    provider=self._embeddings.provider_name,
                )
            replacements.append(
                ReindexReplacement(
                    source_chunk_id=source.chunk_id,
                    document_id=source.document_id,
                    source_chunk=source.chunk,
                    chunks=tuple(part for part, _vector in embedded),
                    embeddings=tuple(vector for _part, vector in embedded),
                )
            )
        return EmbeddedReindex(
            replacements=tuple(replacements),
            embedding_space=space,
        )

    async def persist_prepared_ingest(self, embedded: EmbeddedIngest) -> IngestResult:
        self._validate_embedding_space(embedded.embedding_space)
        repository = self._require_repository()
        prepared = embedded.prepared
        document, created = await repository.add_document(
            owner_id=prepared.owner_id,
            source_type=prepared.source_type,
            source_sha256=prepared.source_sha256,
            title=prepared.title,
            source_uri=prepared.source_uri,
            mime_type=prepared.mime_type,
            sensitivity=prepared.sensitivity,
            retention_class=prepared.retention_class,
            expires_at=prepared.expires_at,
            metadata=prepared.metadata,
            chunks=list(prepared.chunks),
            embeddings=[list(vector) for vector in embedded.embeddings],
            embedding_space=embedded.embedding_space,
        )
        return IngestResult(
            document=document,
            created=created,
            chunk_count=len(prepared.chunks) if created else 0,
        )

    async def ingest_text(
        self,
        *,
        owner_id: str,
        text: str,
        source_type: str = "note",
        title: str | None = None,
        source_uri: str | None = None,
        mime_type: str | None = "text/plain",
        sensitivity: str = "private",
        retention_class: str = "keep",
        metadata: dict[str, Any] | None = None,
    ) -> IngestResult:
        prepared = self.prepare_ingest(
            owner_id=owner_id,
            text=text,
            source_type=source_type,
            title=title,
            source_uri=source_uri,
            mime_type=mime_type,
            sensitivity=sensitivity,
            retention_class=retention_class,
            metadata=metadata,
        )
        # Embedding deliberately precedes the first repository call so callers using
        # this convenience method never hold an implicit database transaction while
        # waiting on the external inference backend.
        embedded = await self.embed_prepared_ingest(prepared)
        return await self.persist_prepared_ingest(embedded)

    async def prepare_search(self, query: str) -> PreparedSearch:
        normalized = _normalize_memory_text(query)
        if not normalized:
            raise ValueError("search query must not be empty")
        prepared_bytes = len(
            (self._embeddings.profile.instruction_for("search_query") + normalized).encode("utf-8")
        )
        if prepared_bytes > self._embeddings.max_text_bytes:
            raise EmbeddingContextError(
                "Search query exceeds the reviewed embedding context.",
                provider=self._embeddings.provider_name,
                maximum_input_bytes=self._embeddings.max_text_bytes,
                input_bytes=prepared_bytes,
                input_index=0,
            )
        response = await self._embeddings.embed([normalized], purpose="search_query")
        space = self._embeddings.embedding_space
        if space is None or response.embedding_space_id != space.space_id:
            raise EmbeddingConfigurationError(
                "Search query was not embedded in the pinned semantic space.",
                provider=self._embeddings.provider_name,
            )
        return PreparedSearch(
            query=normalized,
            embedding=response.embeddings[0],
            embedding_space=space,
        )

    async def search_prepared(
        self,
        *,
        owner_id: str,
        prepared: PreparedSearch,
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        self._validate_embedding_space(prepared.embedding_space)
        repository = self._require_repository()
        inventory = await repository.embedding_inventory(
            owner_id=owner_id,
            embedding_space_id=prepared.embedding_space.space_id,
        )
        if inventory.reindex_required:
            raise EmbeddingConfigurationError(
                "Memory retrieval is unavailable until the approved embedding reindex "
                "covers every active owner-scoped chunk.",
                provider=self._embeddings.provider_name,
            )
        return await repository.search(
            owner_id=owner_id,
            query_text=prepared.query,
            embedding=list(prepared.embedding),
            embedding_space_id=prepared.embedding_space.space_id,
            top_k=top_k,
            hybrid=hybrid,
        )

    async def search(
        self,
        *,
        owner_id: str,
        query: str,
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        prepared = await self.prepare_search(query)
        return await self.search_prepared(
            owner_id=owner_id,
            prepared=prepared,
            top_k=top_k,
            hybrid=hybrid,
        )

    async def get_document(self, *, owner_id: str, document_id: UUID) -> MemoryDocument | None:
        return await self._require_repository().get_document(
            owner_id=owner_id, document_id=document_id
        )

    async def expire_due(self, *, owner_id: str) -> int:
        return await self._require_repository().expire_due(owner_id=owner_id)

    def _require_repository(self) -> MemoryRepository:
        if self._repository is None:
            raise RuntimeError("this memory operation requires a repository")
        return self._repository

    @property
    def _embedding_document_payload_bytes(self) -> int:
        instruction_bytes = len(
            self._embeddings.profile.instruction_for("search_document").encode("utf-8")
        )
        maximum = self._embeddings.max_text_bytes - instruction_bytes
        if maximum < 1:
            raise EmbeddingConfigurationError(
                "Embedding byte ceiling cannot contain the document instruction.",
                provider=self._embeddings.provider_name,
            )
        return maximum

    async def resolve_embedding_space(self) -> EmbeddingSpace:
        """Resolve the active artifact outside caller-owned database transactions."""

        space = await self._embeddings.resolve_space()
        self._validate_embedding_space(space)
        return space

    def _validate_embedding_space(self, space: EmbeddingSpace) -> None:
        expected = EmbeddingSpace.from_profile(
            provider=self._embeddings.provider_name,
            model_alias=self._settings.ollama_embedding_model,
            model_digest=self._settings.ollama_embedding_model_digest,
            dimension=self._settings.embedding_dimensions,
            normalization_policy=self._embeddings.normalization_policy,
            maximum_input_bytes=self._embeddings.max_text_bytes,
            profile=self._embeddings.profile,
        )
        if space != expected:
            raise EmbeddingConfigurationError(
                "Refusing an unreviewed embedding space.",
                provider=self._embeddings.provider_name,
            )

    async def _embed_chunk_recursively(
        self,
        chunk: TextChunk,
        *,
        depth: int = 0,
    ) -> tuple[list[TextChunk], list[tuple[float, ...]]]:
        if depth >= 12 or len(chunk.text) < 2:
            raise EmbeddingContextError(
                "Embedding input remains too large after bounded recursive splitting.",
                provider=self._embeddings.provider_name,
                maximum_input_bytes=self._embeddings.max_text_bytes,
            )
        left, right = _split_text_midpoint(chunk.text)
        parts = [
            TextChunk(
                text=value,
                approximate_tokens=max(1, len(value.split())),
                section_path=chunk.section_path,
                locator=dict(chunk.locator),
            )
            for value in (left, right)
            if value
        ]
        output_chunks: list[TextChunk] = []
        output_vectors: list[tuple[float, ...]] = []
        for part in parts:
            try:
                response = await self._embeddings.embed(
                    [part.text],
                    purpose="search_document",
                )
            except EmbeddingContextError:
                nested_chunks, nested_vectors = await self._embed_chunk_recursively(
                    part,
                    depth=depth + 1,
                )
                output_chunks.extend(nested_chunks)
                output_vectors.extend(nested_vectors)
            else:
                output_chunks.append(part)
                output_vectors.append(response.embeddings[0])
        return output_chunks, output_vectors


def _expiry_for_retention(retention_class: str) -> datetime | None:
    now = datetime.now(UTC)
    if retention_class in {"keep", "legal_hold"}:
        return None
    if retention_class == "ttl_30d":
        return now + timedelta(days=30)
    if retention_class == "ttl_90d":
        return now + timedelta(days=90)
    raise ValueError("unsupported retention class")


def _normalize_memory_text(value: str) -> str:
    """Match the semantic boundary's canonical Unicode and newline preparation."""

    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    ).strip()


def _split_chunks_for_embedding_bytes(
    chunks: list[TextChunk],
    *,
    maximum_bytes: int,
    maximum_characters: int,
) -> list[TextChunk]:
    bounded: list[TextChunk] = []
    for chunk in chunks:
        pending = [chunk.text]
        while pending:
            value = pending.pop(0)
            if len(value.encode("utf-8")) <= maximum_bytes and len(value) <= maximum_characters:
                bounded.append(
                    TextChunk(
                        text=value,
                        approximate_tokens=max(1, len(value.split())),
                        section_path=chunk.section_path,
                        locator=dict(chunk.locator),
                    )
                )
                continue
            left, right = _split_text_midpoint(value)
            if not left or not right:
                raise EmbeddingConfigurationError(
                    "Document text cannot be split below the embedding byte ceiling.",
                    provider="embedding_service",
                )
            pending[0:0] = [left, right]
    return bounded


def _split_text_midpoint(value: str) -> tuple[str, str]:
    midpoint = len(value) // 2
    left_boundary = value.rfind(" ", 0, midpoint + 1)
    right_boundary = value.find(" ", midpoint)
    if left_boundary <= 0 and right_boundary < 0:
        boundary = midpoint
    elif left_boundary <= 0:
        boundary = right_boundary
    elif right_boundary < 0:
        boundary = left_boundary
    else:
        boundary = (
            left_boundary
            if midpoint - left_boundary <= right_boundary - midpoint
            else right_boundary
        )
    return value[:boundary].strip(), value[boundary:].strip()
