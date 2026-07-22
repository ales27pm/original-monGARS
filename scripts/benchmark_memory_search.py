#!/usr/bin/env python3
"""Capture normal and index-forced pgvector ANN plans for an existing owner corpus."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from sqlalchemy import text

from mongars.config import get_settings
from mongars.db.session import Database

_PLAN_SQL = text(
    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
    "SELECT mc.id, mc.embedding <=> CAST(:embedding AS vector) AS distance "
    "FROM memory_chunks AS mc "
    "JOIN memory_documents AS md ON md.id = mc.document_id "
    "WHERE md.owner_id = :owner_id "
    "  AND (md.expires_at IS NULL OR md.expires_at > now()) "
    "ORDER BY mc.embedding <=> CAST(:embedding AS vector) ASC "
    "LIMIT :candidate_count"
)


async def _capture(owner_id: str, candidate_count: int) -> dict[str, Any]:
    settings = get_settings()
    database = Database(settings)
    vector = "[" + ",".join(["1", *("0" for _ in range(settings.embedding_dimensions - 1))]) + "]"
    parameters = {
        "embedding": vector,
        "owner_id": owner_id,
        "candidate_count": candidate_count,
    }
    try:
        async with database.session_factory() as session, session.begin():
            corpus_size = int(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM memory_chunks AS mc "
                        "JOIN memory_documents AS md ON md.id = mc.document_id "
                        "WHERE md.owner_id = :owner_id"
                    ),
                    {"owner_id": owner_id},
                )
                or 0
            )
            normal = (await session.execute(_PLAN_SQL, parameters)).scalar_one()
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            await session.execute(text("SET LOCAL enable_sort = off"))
            index_forced = (await session.execute(_PLAN_SQL, parameters)).scalar_one()
        return {
            "owner_id": owner_id,
            "corpus_chunks": corpus_size,
            "candidate_count": candidate_count,
            "normal_plan": normal,
            "index_forced_plan": index_forced,
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-id", default=get_settings().owner_id)
    parser.add_argument("--candidate-count", type=int, default=64)
    args = parser.parse_args()
    if args.candidate_count < 1 or args.candidate_count > 256:
        parser.error("--candidate-count must be between 1 and 256")
    result = asyncio.run(_capture(args.owner_id, args.candidate_count))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
