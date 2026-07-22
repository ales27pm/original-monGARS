from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import MemoryChunk, MemoryDocument, MemoryDocumentProvenance
from mongars.ids import uuid7
from mongars.memory.chunking import TextChunk


@dataclass(frozen=True, slots=True)
class MemoryHit:
    chunk_id: UUID
    document_id: UUID
    score: float
    text: str
    source_uri: str | None
    title: str | None


class MemoryGovernanceConflict(ValueError):
    """Raised when duplicate content is submitted under a different policy.

    A same-policy duplicate is idempotent at the content layer and records a distinct
    provenance observation. Its original expiry is deliberately preserved: resubmitting
    a TTL-governed document does not silently extend its retention window.
    """


def validate_duplicate_governance(
    document: MemoryDocument,
    *,
    sensitivity: str,
    retention_class: str,
) -> None:
    differences: list[str] = []
    if document.sensitivity != sensitivity:
        differences.append(
            f"sensitivity existing={document.sensitivity!r} requested={sensitivity!r}"
        )
    if document.retention_class != retention_class:
        differences.append(
            f"retention_class existing={document.retention_class!r} requested={retention_class!r}"
        )
    if differences:
        raise MemoryGovernanceConflict(
            "duplicate content conflicts with existing governance: " + "; ".join(differences)
        )


def _provenance_digest(
    *,
    source_type: str,
    source_uri: str | None,
    title: str | None,
    mime_type: str | None,
    metadata: dict[str, Any],
) -> bytes:
    canonical = json.dumps(
        {
            "source_type": source_type,
            "source_uri": source_uri,
            "title": title,
            "mime_type": mime_type,
            "metadata": metadata,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(canonical).digest()


def build_search_statement(
    *,
    owner_id: str,
    query_text: str,
    embedding: list[float],
    top_k: int,
    hybrid: bool,
) -> Select[tuple[Any, ...]]:
    """Build an indexable ANN query followed by bounded hybrid reranking.

    The inner CTE intentionally orders by the raw pgvector cosine distance in
    ascending order. Wrapping the operator in ``1 - distance`` there would prevent
    PostgreSQL from using the HNSW index.
    """

    distance = MemoryChunk.embedding.cosine_distance(embedding)
    candidate_count = min(max(top_k * 8, 32), 256) if hybrid else top_k
    candidates = (
        select(
            MemoryChunk.id.label("chunk_id"),
            distance.label("distance"),
        )
        .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
        .where(
            MemoryDocument.owner_id == owner_id,
            (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
        )
        .order_by(distance.asc())
        .limit(candidate_count)
        .cte("semantic_candidates")
    )

    semantic_score = literal(1.0) - candidates.c.distance
    score = semantic_score
    if hybrid:
        lexical_score = func.ts_rank_cd(
            MemoryChunk.search_vector,
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
        .select_from(candidates)
        .join(MemoryChunk, MemoryChunk.id == candidates.c.chunk_id)
        .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
    )
    if hybrid:
        statement = statement.order_by(
            score.desc(),
            candidates.c.distance.asc(),
            MemoryChunk.id,
        )
    else:
        statement = statement.order_by(candidates.c.distance.asc(), MemoryChunk.id)
    return statement.limit(top_k)


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_digest(
        self,
        *,
        owner_id: str,
        digest: bytes,
        for_update: bool = False,
    ) -> MemoryDocument | None:
        statement = select(MemoryDocument).where(
            MemoryDocument.owner_id == owner_id,
            MemoryDocument.source_sha256 == digest,
        )
        if for_update:
            statement = statement.with_for_update()
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
            validate_duplicate_governance(
                existing,
                sensitivity=sensitivity,
                retention_class=retention_class,
            )
            await self.add_provenance(
                document_id=existing.id,
                source_type=source_type,
                source_uri=source_uri,
                title=title,
                mime_type=mime_type,
                metadata=metadata,
            )
            return existing, False
        if len(chunks) != len(embeddings):
            raise ValueError("each chunk must have exactly one embedding")

        document_id = uuid7()
        insert_document = (
            pg_insert(MemoryDocument)
            .values(
                id=document_id,
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
            .on_conflict_do_nothing(constraint="uq_memory_document_owner_sha")
            .returning(MemoryDocument.id)
        )
        inserted_id = await self._session.scalar(insert_document)
        if inserted_id is None:
            # A concurrent writer won the content-addressed insert. Lock and validate
            # its policy before accepting this submission as an idempotent duplicate.
            winner = await self.find_by_digest(
                owner_id=owner_id,
                digest=source_sha256,
                for_update=True,
            )
            if winner is None:
                raise RuntimeError("content insert conflicted but the winning row is missing")
            validate_duplicate_governance(
                winner,
                sensitivity=sensitivity,
                retention_class=retention_class,
            )
            await self.add_provenance(
                document_id=winner.id,
                source_type=source_type,
                source_uri=source_uri,
                title=title,
                mime_type=mime_type,
                metadata=metadata,
            )
            return winner, False

        document = await self._session.get(MemoryDocument, inserted_id)
        if document is None:
            raise RuntimeError("inserted memory document could not be reloaded")
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
        await self.add_provenance(
            document_id=document.id,
            source_type=source_type,
            source_uri=source_uri,
            title=title,
            mime_type=mime_type,
            metadata=metadata,
        )
        await self._session.flush()
        return document, True

    async def add_provenance(
        self,
        *,
        document_id: UUID,
        source_type: str,
        source_uri: str | None,
        title: str | None,
        mime_type: str | None,
        metadata: dict[str, Any],
    ) -> None:
        digest = _provenance_digest(
            source_type=source_type,
            source_uri=source_uri,
            title=title,
            mime_type=mime_type,
            metadata=metadata,
        )
        statement = (
            pg_insert(MemoryDocumentProvenance)
            .values(
                id=uuid7(),
                document_id=document_id,
                provenance_sha256=digest,
                source_type=source_type,
                source_uri=source_uri,
                title=title,
                mime_type=mime_type,
                metadata_json=metadata,
            )
            .on_conflict_do_nothing(constraint="uq_memory_document_provenance_digest")
        )
        await self._session.execute(statement)

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
        statement = build_search_statement(
            owner_id=owner_id,
            query_text=query_text,
            embedding=embedding,
            top_k=top_k,
            hybrid=hybrid,
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
