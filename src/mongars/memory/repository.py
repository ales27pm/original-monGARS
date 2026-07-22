from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import MemoryChunk, MemoryDocument
from mongars.memory.chunking import TextChunk


@dataclass(frozen=True, slots=True)
class MemoryHit:
    chunk_id: UUID
    document_id: UUID
    score: float
    text: str
    source_uri: str | None
    title: str | None


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_digest(self, *, owner_id: str, digest: bytes) -> MemoryDocument | None:
        statement = select(MemoryDocument).where(
            MemoryDocument.owner_id == owner_id,
            MemoryDocument.source_sha256 == digest,
        )
        return cast(MemoryDocument | None, await self._session.scalar(statement))

    async def has_documents(self, *, owner_id: str) -> bool:
        statement = (
            select(MemoryDocument.id)
            .where(
                MemoryDocument.owner_id == owner_id,
                (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
            )
            .limit(1)
        )
        return await self._session.scalar(statement) is not None

    async def add_document(
        self,
        *,
        owner_id: str,
        source_type: str,
        source_sha256: bytes,
        title: str | None,
        source_uri: str | None,
        mime_type: str | None,
        sensitivity: str,
        retention_class: str,
        expires_at: datetime | None,
        metadata: dict[str, Any],
        chunks: list[TextChunk],
        embeddings: list[list[float]],
        embedding_model: str,
    ) -> tuple[MemoryDocument, bool]:
        existing = await self.find_by_digest(owner_id=owner_id, digest=source_sha256)
        if existing is not None:
            return existing, False
        if len(chunks) != len(embeddings):
            raise ValueError("each chunk must have exactly one embedding")

        document = MemoryDocument(
            owner_id=owner_id,
            source_type=source_type,
            source_uri=source_uri,
            source_sha256=source_sha256,
            title=title,
            mime_type=mime_type,
            sensitivity=sensitivity,
            retention_class=retention_class,
            expires_at=expires_at,
            metadata_json=metadata,
        )
        self._session.add(document)
        await self._session.flush()
        self._session.add_all(
            MemoryChunk(
                document_id=document.id,
                chunk_index=index,
                token_count=chunk.approximate_tokens,
                char_count=len(chunk.text),
                section_path=list(chunk.section_path),
                plaintext=chunk.text,
                embedding=embedding,
                embedding_model=embedding_model,
            )
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True))
        )
        await self._session.flush()
        return document, True

    async def get_document(self, *, owner_id: str, document_id: UUID) -> MemoryDocument | None:
        statement = select(MemoryDocument).where(
            MemoryDocument.id == document_id,
            MemoryDocument.owner_id == owner_id,
        )
        return cast(MemoryDocument | None, await self._session.scalar(statement))

    async def search(
        self,
        *,
        owner_id: str,
        query_text: str,
        embedding: list[float],
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        distance = MemoryChunk.embedding.cosine_distance(embedding)
        semantic_score = 1.0 - distance
        score = semantic_score
        if hybrid:
            lexical_score = func.ts_rank_cd(
                func.to_tsvector("simple", MemoryChunk.plaintext),
                func.plainto_tsquery("simple", query_text),
            )
            score = (semantic_score * 0.85) + (func.least(lexical_score, 1.0) * 0.15)

        statement = (
            select(
                MemoryChunk.id,
                MemoryChunk.document_id,
                score.label("score"),
                MemoryChunk.plaintext,
                MemoryDocument.source_uri,
                MemoryDocument.title,
            )
            .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
            .where(
                MemoryDocument.owner_id == owner_id,
                (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
            )
            .order_by(score.desc())
            .limit(top_k)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            MemoryHit(
                chunk_id=row.id,
                document_id=row.document_id,
                score=float(row.score),
                text=row.plaintext,
                source_uri=row.source_uri,
                title=row.title,
            )
            for row in rows
        ]

    async def expire_due(self, *, owner_id: str) -> int:
        statement = (
            delete(MemoryDocument)
            .where(
                MemoryDocument.owner_id == owner_id,
                MemoryDocument.expires_at.is_not(None),
                MemoryDocument.expires_at <= datetime.now(UTC),
                MemoryDocument.retention_class != "legal_hold",
            )
            .returning(MemoryDocument.id)
        )
        return len((await self._session.scalars(statement)).all())
