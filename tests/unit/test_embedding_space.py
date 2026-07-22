from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any

import pytest

from mongars.embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProfile,
    EmbeddingService,
    EmbeddingSpace,
)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def test_space_id_is_canonical_stable_and_sensitive_to_every_policy_axis() -> None:
    profile = EmbeddingProfile()
    base = EmbeddingSpace.from_profile(
        provider="ollama",
        model_alias="nomic-embed-text",
        model_digest="a" * 64,
        dimension=768,
        normalization_policy="none",
        maximum_input_bytes=8_192,
        profile=profile,
    )
    identical = EmbeddingSpace.from_profile(
        provider="ollama",
        model_alias="nomic-embed-text",
        model_digest="a" * 64,
        dimension=768,
        normalization_policy="none",
        maximum_input_bytes=8_192,
        profile=profile,
    )

    assert base.space_id == identical.space_id
    assert len(base.space_id) == 64
    assert replace(base, model_digest="b" * 64).space_id != base.space_id
    assert replace(base, normalization_policy="l2").space_id != base.space_id
    assert replace(base, query_instruction="query: ").space_id != base.space_id
    assert replace(base, maximum_input_bytes=4_096).space_id != base.space_id
    assert replace(base, profile_version="v2").space_id != base.space_id


def test_profile_cannot_enable_provider_truncation() -> None:
    with pytest.raises(ValueError, match="never enable truncation"):
        EmbeddingProfile(truncate=True)


def test_deterministic_provider_is_reproducible_and_uses_service_preparation() -> None:
    first_provider = DeterministicEmbeddingProvider(dimension=5)
    second_provider = DeterministicEmbeddingProvider(dimension=5)
    first = EmbeddingService(
        provider=first_provider,
        expected_dimension=5,
        batch_size=2,
    )
    second = EmbeddingService(
        provider=second_provider,
        expected_dimension=5,
        batch_size=2,
    )

    first_result = run(first.embed(["bonjour"], purpose="search_query"))
    second_result = run(second.embed(["bonjour"], purpose="search_query"))

    assert first_provider.calls == [("search_query: bonjour",)]
    assert first_result.embeddings == second_result.embeddings
    assert first_result.model_digest == second_result.model_digest
    assert first_result.embedding_space_id == second_result.embedding_space_id
