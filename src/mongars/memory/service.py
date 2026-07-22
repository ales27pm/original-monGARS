from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mongars.config import Settings
from mongars.db.models import MemoryDocument
from mongars.embeddings.errors import EmbeddingConfigurationError
from mongars.embeddings.service import EmbeddingService
from mongars.memory.chunking import TextChunk, chunk_text
from mongars.memory.repository import (
    MemoryHit,
    MemoryRepository,
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
    embedding_model: str


@dataclass(frozen=True, slots=True)
class PreparedSearch:
    query: str
    embedding: tuple[float, ...]
    embedding_model: str


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
    ) -> PreparedIngest:
        normalized = text.strip()
        if not normalized:
            raise ValueError("document text must not be empty")
        if len(normalized) > self._settings.max_document_chars:
            raise ValueError("document exceeds the configured character limit")

        chunks = chunk_text(
            normalized,
            max_tokens=self._settings.memory_chunk_tokens,
            overlap_tokens=self._settings.memory_chunk_overlap_tokens,
            max_characters=self._settings.memory_chunk_characters,
        )
        return PreparedIngest(
            owner_id=owner_id,
            source_sha256=hashlib.sha256(normalized.encode()).digest(),
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

    async def resolve_existing_ingest(self, prepared: PreparedIngest) -> IngestResult | None:
        """Resolve a duplicate in a short transaction before external embedding.

        Equal content is only idempotent when sensitivity and retention match. A
        same-retention TTL submission preserves the original expiry rather than
        extending it. Every distinct source observation is recorded separately.
        """

        repository = self._require_repository()
        existing = await repository.find_by_digest(
            owner_id=prepared.owner_id,
            digest=prepared.source_sha256,
        )
        if existing is None:
            return None
        await repository.require_document_embedding_model(
            document_id=existing.id,
            embedding_model=self._settings.ollama_embedding_model,
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
        model: str | None = None
        pending: list[str] = []
        pending_characters = 0

        async def flush() -> None:
            nonlocal model, pending, pending_characters
            if not pending:
                return
            response = await self._embeddings.embed(pending)
            if model is not None and response.model != model:
                raise EmbeddingConfigurationError(
                    "Embedding model changed while processing one document.",
                    provider=self._embeddings.provider_name,
                )
            model = response.model
            vectors.extend(response.embeddings)
            pending = []
            pending_characters = 0

        for chunk in prepared.chunks:
            chunk_characters = len(chunk.text)
            if chunk_characters > self._embeddings.max_text_characters:
                raise EmbeddingConfigurationError(
                    "A memory chunk exceeds the embedding service text boundary.",
                    provider=self._embeddings.provider_name,
                )
            if pending and (
                len(pending) >= self._embeddings.max_inputs
                or pending_characters + chunk_characters > self._embeddings.max_total_characters
            ):
                await flush()
            pending.append(chunk.text)
            pending_characters += chunk_characters
        await flush()
        if model is None:
            raise EmbeddingConfigurationError(
                "Prepared ingestion produced no embeddable chunks.",
                provider=self._embeddings.provider_name,
            )
        return EmbeddedIngest(
            prepared=prepared,
            embeddings=tuple(vectors),
            embedding_model=model,
        )

    async def persist_prepared_ingest(self, embedded: EmbeddedIngest) -> IngestResult:
        if embedded.embedding_model != self._settings.ollama_embedding_model:
            raise EmbeddingConfigurationError(
                "Refusing to persist vectors from an unreviewed embedding model.",
                provider=self._embeddings.provider_name,
            )
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
            embedding_model=embedded.embedding_model,
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
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must not be empty")
        response = await self._embeddings.embed([normalized])
        return PreparedSearch(
            query=normalized,
            embedding=response.embeddings[0],
            embedding_model=response.model,
        )

    async def search_prepared(
        self,
        *,
        owner_id: str,
        prepared: PreparedSearch,
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        if prepared.embedding_model != self._settings.ollama_embedding_model:
            raise EmbeddingConfigurationError(
                "Refusing to search memory with an unreviewed embedding model.",
                provider=self._embeddings.provider_name,
            )
        return await self._require_repository().search(
            owner_id=owner_id,
            query_text=prepared.query,
            embedding=list(prepared.embedding),
            embedding_model=prepared.embedding_model,
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


def _expiry_for_retention(retention_class: str) -> datetime | None:
    now = datetime.now(UTC)
    if retention_class in {"keep", "legal_hold"}:
        return None
    if retention_class == "ttl_30d":
        return now + timedelta(days=30)
    if retention_class == "ttl_90d":
        return now + timedelta(days=90)
    raise ValueError("unsupported retention class")
