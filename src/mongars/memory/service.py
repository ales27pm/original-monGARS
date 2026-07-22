from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from mongars.config import Settings
from mongars.db.models import MemoryDocument
from mongars.inference.base import InferenceBackend
from mongars.memory.chunking import chunk_text
from mongars.memory.repository import MemoryHit, MemoryRepository


@dataclass(frozen=True, slots=True)
class IngestResult:
    document: MemoryDocument
    created: bool
    chunk_count: int


class MemoryService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: MemoryRepository,
        inference: InferenceBackend,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._inference = inference

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
        normalized = text.strip()
        if not normalized:
            raise ValueError("document text must not be empty")
        if len(normalized) > self._settings.max_document_chars:
            raise ValueError("document exceeds the configured character limit")

        digest = hashlib.sha256(normalized.encode()).digest()
        existing = await self._repository.find_by_digest(owner_id=owner_id, digest=digest)
        if existing is not None:
            return IngestResult(document=existing, created=False, chunk_count=0)

        chunks = chunk_text(
            normalized,
            max_tokens=self._settings.memory_chunk_tokens,
            overlap_tokens=self._settings.memory_chunk_overlap_tokens,
        )
        embeddings: list[list[float]] = []
        embedding_model = self._settings.ollama_embedding_model
        batch_size = self._settings.embedding_batch_size
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            response = await self._inference.embed(
                [chunk.text for chunk in batch],
                expected_dimension=self._settings.embedding_dimensions,
            )
            embedding_model = response.model
            embeddings.extend([list(vector) for vector in response.embeddings])

        expires_at = _expiry_for_retention(retention_class)
        document, created = await self._repository.add_document(
            owner_id=owner_id,
            source_type=source_type,
            source_sha256=digest,
            title=title,
            source_uri=source_uri,
            mime_type=mime_type,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=expires_at,
            metadata=metadata or {},
            chunks=chunks,
            embeddings=embeddings,
            embedding_model=embedding_model,
        )
        return IngestResult(document=document, created=created, chunk_count=len(chunks))

    async def search(
        self,
        *,
        owner_id: str,
        query: str,
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        normalized = query.strip()
        if not normalized:
            raise ValueError("search query must not be empty")
        response = await self._inference.embed(
            [normalized], expected_dimension=self._settings.embedding_dimensions
        )
        return await self._repository.search(
            owner_id=owner_id,
            query_text=normalized,
            embedding=list(response.embeddings[0]),
            top_k=top_k,
            hybrid=hybrid,
        )

    async def get_document(self, *, owner_id: str, document_id: UUID) -> MemoryDocument | None:
        return await self._repository.get_document(owner_id=owner_id, document_id=document_id)

    async def expire_due(self, *, owner_id: str) -> int:
        return await self._repository.expire_due(owner_id=owner_id)


def _expiry_for_retention(retention_class: str) -> datetime | None:
    now = datetime.now(UTC)
    if retention_class in {"keep", "legal_hold"}:
        return None
    if retention_class == "ttl_30d":
        return now + timedelta(days=30)
    if retention_class == "ttl_90d":
        return now + timedelta(days=90)
    raise ValueError("unsupported retention class")
