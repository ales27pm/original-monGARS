from __future__ import annotations

from sqlalchemy.dialects import postgresql

from mongars.memory.repository import build_search_statement


def _unit_vector() -> list[float]:
    return [1.0, *([0.0] * 767)]


def test_semantic_candidates_order_by_raw_distance_before_similarity_projection() -> None:
    statement = build_search_statement(
        owner_id="owner",
        query_text="query",
        embedding=_unit_vector(),
        embedding_model="nomic-embed-text",
        top_k=8,
        hybrid=False,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    cte, outer = sql.split(")\n SELECT", maxsplit=1)
    assert "ORDER BY (memory_chunks.embedding <=>" in cte
    assert " ASC" in cte
    assert "1.0 -" not in cte
    assert "semantic_candidates.distance" in outer
    assert "memory_chunks.embedding_model =" in cte
    assert statement.compile(dialect=postgresql.dialect()).params["embedding_model_1"] == (
        "nomic-embed-text"
    )


def test_hybrid_reranking_uses_stored_vector_on_bounded_ann_candidates() -> None:
    statement = build_search_statement(
        owner_id="owner",
        query_text="exact phrase",
        embedding=_unit_vector(),
        embedding_model="nomic-embed-text",
        top_k=8,
        hybrid=True,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "LIMIT %(" in sql
    assert "memory_chunks.search_vector" in sql
    assert "to_tsvector" not in sql
    assert "ts_rank_cd" in sql
