from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.db.models import (
    MemoryChunk,
    MemoryChunkEmbedding,
    MemoryDocument,
    MemoryDocumentProvenance,
)
from mongars.embeddings.models import EmbeddingSpace
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
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmbeddingInventory:
    compatible_chunk_count: int
    legacy_chunk_count: int

    @property
    def reindex_required(self) -> bool:
        return self.legacy_chunk_count > 0


@dataclass(frozen=True, slots=True)
class ReindexChunk:
    chunk_id: UUID
    document_id: UUID
    chunk: TextChunk


@dataclass(frozen=True, slots=True)
class ReindexReplacement:
    """One stale chunk and its active-space, locator-preserving replacement."""

    source_chunk_id: UUID
    document_id: UUID
    source_chunk: TextChunk
    chunks: tuple[TextChunk, ...]
    embeddings: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class ReindexApplyResult:
    source_chunk_count: int
    active_chunk_count: int


class MemoryGovernanceConflict(ValueError):
    """Raised when duplicate content is submitted under a different policy.

    A same-policy duplicate is idempotent at the content layer and records a distinct
    provenance observation. Its original expiry is deliberately preserved: resubmitting
    a TTL-governed document does not silently extend its retention window.
    """


class MemoryEmbeddingModelConflict(MemoryGovernanceConflict):
    """Duplicate content exists under a different semantic vector space.

    Treat this as a terminal, reviewable conflict rather than reporting an
    idempotent success for content that the active retrieval model cannot see.
    An explicit reindex workflow is required to move the existing document.
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
    embedding_space_id: str,
    top_k: int,
    hybrid: bool,
) -> Select[tuple[Any, ...]]:
    """Build an indexable ANN query followed by bounded hybrid reranking.

    The inner CTE intentionally orders by the raw pgvector cosine distance in
    ascending order. Wrapping the operator in ``1 - distance`` there would prevent
    PostgreSQL from using the HNSW index.
    """

    distance = MemoryChunkEmbedding.embedding.cosine_distance(embedding)
    candidate_count = min(max(top_k * 8, 32), 256) if hybrid else top_k
    candidates = (
        select(
            MemoryChunk.id.label("chunk_id"),
            distance.label("distance"),
        )
        .join(MemoryChunkEmbedding, MemoryChunkEmbedding.chunk_id == MemoryChunk.id)
        .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
        .where(
            MemoryDocument.owner_id == owner_id,
            MemoryChunkEmbedding.embedding_space_id == embedding_space_id,
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
            MemoryChunk.locator,
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
            (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
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

    async def require_document_embedding_space(
        self,
        *,
        document_id: UUID,
        embedding_space_id: str,
    ) -> None:
        """Fail closed unless every chunk has a vector in the exact active space."""

        chunk_count = int(
            await self._session.scalar(
                select(func.count(MemoryChunk.id)).where(MemoryChunk.document_id == document_id)
            )
            or 0
        )
        compatible_count = int(
            await self._session.scalar(
                select(func.count(MemoryChunkEmbedding.chunk_id))
                .join(MemoryChunk, MemoryChunk.id == MemoryChunkEmbedding.chunk_id)
                .where(
                    MemoryChunk.document_id == document_id,
                    MemoryChunkEmbedding.embedding_space_id == embedding_space_id,
                )
            )
            or 0
        )
        if chunk_count == 0 or compatible_count != chunk_count:
            raise MemoryEmbeddingModelConflict(
                "duplicate content does not have a complete vector set in the active embedding "
                "space; explicit reindex is required"
            )

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
        embedding_space: EmbeddingSpace,
    ) -> tuple[MemoryDocument, bool]:
        existing = await self.find_by_digest(owner_id=owner_id, digest=source_sha256)
        if existing is not None:
            await self.require_document_embedding_space(
                document_id=existing.id,
                embedding_space_id=embedding_space.space_id,
            )
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

        # An expired TTL row is no longer a valid deduplication target, but the
        # content-addressed uniqueness constraint still occupies its owner/digest
        # key until the periodic retention sweep runs. Remove only that inactive
        # row in this short persistence transaction so the new governed document
        # can be inserted atomically. Concurrent creators still converge through
        # the unique-key conflict path below.
        await self._session.execute(
            delete(MemoryDocument).where(
                MemoryDocument.owner_id == owner_id,
                MemoryDocument.source_sha256 == source_sha256,
                MemoryDocument.expires_at.is_not(None),
                MemoryDocument.expires_at <= func.now(),
                MemoryDocument.retention_class != "legal_hold",
            )
        )

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
            await self.require_document_embedding_space(
                document_id=winner.id,
                embedding_space_id=embedding_space.space_id,
            )
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
        stored_chunks = [
            MemoryChunk(
                id=uuid7(),
                document_id=document.id,
                chunk_index=index,
                token_count=chunk.approximate_tokens,
                char_count=len(chunk.text),
                section_path=list(chunk.section_path),
                locator=dict(chunk.locator),
                plaintext=chunk.text,
            )
            for index, chunk in enumerate(chunks)
        ]
        self._session.add_all(stored_chunks)
        self._session.add_all(
            MemoryChunkEmbedding(
                chunk_id=chunk.id,
                embedding_space_id=embedding_space.space_id,
                provider=embedding_space.provider,
                model_alias=embedding_space.model_alias,
                model_digest=embedding_space.model_digest,
                dimension=embedding_space.dimension,
                normalization_policy=embedding_space.normalization_policy,
                document_instruction=embedding_space.document_instruction,
                query_instruction=embedding_space.query_instruction,
                clustering_instruction=embedding_space.clustering_instruction,
                classification_instruction=embedding_space.classification_instruction,
                truncate=embedding_space.truncate,
                maximum_input_bytes=embedding_space.maximum_input_bytes,
                profile_version=embedding_space.profile_version,
                embedding=embedding,
            )
            for chunk, embedding in zip(stored_chunks, embeddings, strict=True)
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
            (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
        )
        return cast(MemoryDocument | None, await self._session.scalar(statement))

    async def search(
        self,
        *,
        owner_id: str,
        query_text: str,
        embedding: list[float],
        embedding_space_id: str,
        top_k: int,
        hybrid: bool = True,
    ) -> list[MemoryHit]:
        # Owner and semantic-space filters are applied after an approximate HNSW
        # scan. Iterative scanning prevents foreign-owner or legacy-space neighbors
        # from consuming the initial candidate list and starving valid results.
        await self._session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
        statement = build_search_statement(
            owner_id=owner_id,
            query_text=query_text,
            embedding=embedding,
            embedding_space_id=embedding_space_id,
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
                locator=dict(row.locator),
            )
            for row in rows
        ]

    async def embedding_inventory(
        self,
        *,
        owner_id: str,
        embedding_space_id: str,
    ) -> EmbeddingInventory:
        total = int(
            await self._session.scalar(
                select(func.count(MemoryChunk.id))
                .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
                .where(
                    MemoryDocument.owner_id == owner_id,
                    (
                        MemoryDocument.expires_at.is_(None)
                        | (MemoryDocument.expires_at > func.now())
                    ),
                )
            )
            or 0
        )
        compatible = int(
            await self._session.scalar(
                select(func.count(MemoryChunkEmbedding.chunk_id))
                .join(MemoryChunk, MemoryChunk.id == MemoryChunkEmbedding.chunk_id)
                .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
                .where(
                    MemoryDocument.owner_id == owner_id,
                    MemoryChunkEmbedding.embedding_space_id == embedding_space_id,
                    (
                        MemoryDocument.expires_at.is_(None)
                        | (MemoryDocument.expires_at > func.now())
                    ),
                )
            )
            or 0
        )
        return EmbeddingInventory(
            compatible_chunk_count=compatible,
            legacy_chunk_count=max(0, total - compatible),
        )

    async def list_reindex_chunks(
        self,
        *,
        owner_id: str,
        embedding_space_id: str,
        limit: int,
        document_id: UUID | None = None,
    ) -> list[ReindexChunk]:
        active_vector = (
            select(MemoryChunkEmbedding.chunk_id)
            .where(
                MemoryChunkEmbedding.chunk_id == MemoryChunk.id,
                MemoryChunkEmbedding.embedding_space_id == embedding_space_id,
            )
            .exists()
        )
        statement = (
            select(MemoryChunk)
            .join(MemoryDocument, MemoryDocument.id == MemoryChunk.document_id)
            .where(
                MemoryDocument.owner_id == owner_id,
                ~active_vector,
                (MemoryDocument.expires_at.is_(None) | (MemoryDocument.expires_at > func.now())),
            )
            .order_by(MemoryChunk.document_id, MemoryChunk.chunk_index, MemoryChunk.id)
            .limit(limit)
        )
        if document_id is not None:
            statement = statement.where(MemoryChunk.document_id == document_id)
        chunks = list((await self._session.scalars(statement)).all())
        return [
            ReindexChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                chunk=TextChunk(
                    text=chunk.plaintext,
                    approximate_tokens=chunk.token_count or max(1, len(chunk.plaintext.split())),
                    section_path=tuple(chunk.section_path),
                    locator=dict(chunk.locator),
                ),
            )
            for chunk in chunks
        ]

    async def apply_reindex_replacements(
        self,
        *,
        owner_id: str,
        replacements: list[ReindexReplacement],
        embedding_space: EmbeddingSpace,
    ) -> ReindexApplyResult:
        """Atomically apply embeddings or split oversized legacy chunks.

        The database rows are locked and rechecked for both owner and active-space
        coverage after the external embedding call. A stale concurrent reindex can
        therefore neither modify another owner's data nor replace a chunk that has
        already been covered. Splitting keeps the original chunk identifier for the
        first part, copies its structured locator to every part, and leaves document
        provenance untouched.
        """

        if not replacements:
            return ReindexApplyResult(source_chunk_count=0, active_chunk_count=0)
        replacement_by_id = {item.source_chunk_id: item for item in replacements}
        if len(replacement_by_id) != len(replacements):
            raise ValueError("reindex replacements must have unique source chunk IDs")
        for item in replacements:
            if not item.chunks or len(item.chunks) != len(item.embeddings):
                raise ValueError("each reindex replacement needs one embedding per chunk")

        requested_document_ids = sorted(
            {item.document_id for item in replacements},
            key=str,
        )
        owned_documents = list(
            (
                await self._session.scalars(
                    select(MemoryDocument)
                    .where(
                        MemoryDocument.id.in_(requested_document_ids),
                        MemoryDocument.owner_id == owner_id,
                        (
                            MemoryDocument.expires_at.is_(None)
                            | (MemoryDocument.expires_at > func.now())
                        ),
                    )
                    .order_by(MemoryDocument.id)
                    .with_for_update()
                )
            ).all()
        )
        owned_document_ids = {document.id for document in owned_documents}
        if not owned_document_ids:
            return ReindexApplyResult(source_chunk_count=0, active_chunk_count=0)

        document_chunks = list(
            (
                await self._session.scalars(
                    select(MemoryChunk)
                    .where(MemoryChunk.document_id.in_(owned_document_ids))
                    .order_by(
                        MemoryChunk.document_id,
                        MemoryChunk.chunk_index,
                        MemoryChunk.id,
                    )
                    .with_for_update()
                )
            ).all()
        )
        requested_chunk_ids = set(replacement_by_id)
        covered_chunk_ids = set(
            (
                await self._session.scalars(
                    select(MemoryChunkEmbedding.chunk_id).where(
                        MemoryChunkEmbedding.chunk_id.in_(requested_chunk_ids),
                        MemoryChunkEmbedding.embedding_space_id == embedding_space.space_id,
                    )
                )
            ).all()
        )
        eligible_rows: dict[UUID, MemoryChunk] = {}
        for row in document_chunks:
            replacement = replacement_by_id.get(row.id)
            if (
                replacement is None
                or row.document_id not in owned_document_ids
                or row.id in covered_chunk_ids
            ):
                continue
            if replacement.document_id != row.document_id:
                raise ValueError("reindex replacement document identity changed")
            if (
                replacement.source_chunk.text != row.plaintext
                or replacement.source_chunk.section_path != tuple(row.section_path)
                or replacement.source_chunk.locator != dict(row.locator)
            ):
                raise ValueError("reindex source chunk changed before persistence")
            eligible_rows[row.id] = row

        if not eligible_rows:
            return ReindexApplyResult(source_chunk_count=0, active_chunk_count=0)

        split_source_ids = {
            chunk_id
            for chunk_id, row in eligible_rows.items()
            if (
                len(replacement_by_id[chunk_id].chunks) != 1
                or replacement_by_id[chunk_id].chunks[0].text != row.plaintext
            )
        }
        if split_source_ids:
            # Once plaintext is divided, every vector derived from the prior complete
            # text is stale. Remove both shadow-space rows and the rolling-upgrade
            # compatibility values before changing the durable chunk.
            await self._session.execute(
                delete(MemoryChunkEmbedding).where(
                    MemoryChunkEmbedding.chunk_id.in_(split_source_ids)
                )
            )
            for chunk_id in sorted(split_source_ids, key=str):
                await self._session.execute(
                    text(
                        "UPDATE memory_chunks SET embedding = NULL, embedding_model = NULL "
                        "WHERE id = :chunk_id"
                    ),
                    {"chunk_id": chunk_id},
                )

        rows_by_document: dict[UUID, list[MemoryChunk]] = {}
        for row in document_chunks:
            rows_by_document.setdefault(row.document_id, []).append(row)

        replacement_rows: dict[UUID, tuple[MemoryChunk, ...]] = {}
        for document_id, rows in rows_by_document.items():
            split_rows = [row for row in rows if row.id in split_source_ids]
            if not split_rows:
                continue
            extra_count = sum(len(replacement_by_id[row.id].chunks) - 1 for row in split_rows)
            maximum_index = max((row.chunk_index for row in rows), default=-1)
            temporary_offset = maximum_index + len(rows) + extra_count + 1
            for offset, row in enumerate(rows):
                row.chunk_index = temporary_offset + offset
            await self._session.flush()

            desired_rows: list[MemoryChunk] = []
            next_temporary_index = temporary_offset + len(rows)
            for row in rows:
                replacement = replacement_by_id.get(row.id)
                if row.id not in split_source_ids or replacement is None:
                    desired_rows.append(row)
                    continue
                part_rows: list[MemoryChunk] = []
                for part_index, part in enumerate(replacement.chunks):
                    if part_index == 0:
                        part_row = row
                        part_row.plaintext = part.text
                        part_row.token_count = part.approximate_tokens
                        part_row.char_count = len(part.text)
                        part_row.section_path = list(part.section_path)
                        part_row.locator = dict(part.locator)
                    else:
                        part_row = MemoryChunk(
                            id=uuid7(),
                            document_id=document_id,
                            chunk_index=next_temporary_index,
                            token_count=part.approximate_tokens,
                            char_count=len(part.text),
                            section_path=list(part.section_path),
                            locator=dict(part.locator),
                            plaintext=part.text,
                        )
                        next_temporary_index += 1
                        self._session.add(part_row)
                    part_rows.append(part_row)
                    desired_rows.append(part_row)
                replacement_rows[row.id] = tuple(part_rows)

            await self._session.flush()
            for final_index, row in enumerate(desired_rows):
                row.chunk_index = final_index
            await self._session.flush()

        inserted_source_count = 0
        inserted_chunk_count = 0
        for source_chunk_id, row in eligible_rows.items():
            replacement = replacement_by_id[source_chunk_id]
            persisted_rows = replacement_rows.get(source_chunk_id, (row,))
            source_inserted = 0
            for part_row, embedding in zip(
                persisted_rows,
                replacement.embeddings,
                strict=True,
            ):
                statement = (
                    pg_insert(MemoryChunkEmbedding)
                    .values(
                        chunk_id=part_row.id,
                        embedding_space_id=embedding_space.space_id,
                        provider=embedding_space.provider,
                        model_alias=embedding_space.model_alias,
                        model_digest=embedding_space.model_digest,
                        dimension=embedding_space.dimension,
                        normalization_policy=embedding_space.normalization_policy,
                        document_instruction=embedding_space.document_instruction,
                        query_instruction=embedding_space.query_instruction,
                        clustering_instruction=embedding_space.clustering_instruction,
                        classification_instruction=embedding_space.classification_instruction,
                        truncate=embedding_space.truncate,
                        maximum_input_bytes=embedding_space.maximum_input_bytes,
                        profile_version=embedding_space.profile_version,
                        embedding=list(embedding),
                    )
                    .on_conflict_do_nothing()
                    .returning(MemoryChunkEmbedding.chunk_id)
                )
                if await self._session.scalar(statement) is not None:
                    source_inserted += 1
                    inserted_chunk_count += 1
            if source_inserted:
                inserted_source_count += 1
        return ReindexApplyResult(
            source_chunk_count=inserted_source_count,
            active_chunk_count=inserted_chunk_count,
        )

    async def add_reindexed_embeddings(
        self,
        *,
        chunks: list[ReindexChunk],
        embeddings: list[list[float]],
        embedding_space: EmbeddingSpace,
    ) -> int:
        if len(chunks) != len(embeddings):
            raise ValueError("each reindex chunk must have exactly one embedding")
        inserted = 0
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            statement = (
                pg_insert(MemoryChunkEmbedding)
                .values(
                    chunk_id=chunk.chunk_id,
                    embedding_space_id=embedding_space.space_id,
                    provider=embedding_space.provider,
                    model_alias=embedding_space.model_alias,
                    model_digest=embedding_space.model_digest,
                    dimension=embedding_space.dimension,
                    normalization_policy=embedding_space.normalization_policy,
                    document_instruction=embedding_space.document_instruction,
                    query_instruction=embedding_space.query_instruction,
                    clustering_instruction=embedding_space.clustering_instruction,
                    classification_instruction=embedding_space.classification_instruction,
                    truncate=embedding_space.truncate,
                    maximum_input_bytes=embedding_space.maximum_input_bytes,
                    profile_version=embedding_space.profile_version,
                    embedding=embedding,
                )
                .on_conflict_do_nothing()
                .returning(MemoryChunkEmbedding.chunk_id)
            )
            if await self._session.scalar(statement) is not None:
                inserted += 1
        return inserted

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
