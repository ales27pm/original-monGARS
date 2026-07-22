from __future__ import annotations

import asyncio
import math
from collections.abc import Coroutine, Sequence
from typing import Any, cast

import pytest

from mongars.embeddings import (
    EmbeddingBatch,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingInputError,
    EmbeddingMetric,
    EmbeddingModelMismatchError,
    EmbeddingResponseError,
    EmbeddingService,
)


def run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


class DeterministicProvider:
    provider_name = "deterministic"

    def __init__(self, *, model: str = "test-embed", dimension: int = 3) -> None:
        self.model_name = model
        self.dimension = dimension
        self.calls: list[tuple[str, ...]] = []
        self.response_model = model
        self.response_dimension = dimension
        self.response_count_delta = 0
        self.vector_override: tuple[float, ...] | None = None
        self.mutate_model_after_call: str | None = None
        self.closed = False

    async def embed(
        self,
        texts: Sequence[str],
        *,
        expected_dimension: int,
    ) -> EmbeddingBatch:
        assert expected_dimension == self.dimension
        normalized = tuple(texts)
        self.calls.append(normalized)
        count = len(normalized) + self.response_count_delta
        vector = self.vector_override or tuple(float(index + 1) for index in range(self.dimension))
        result = EmbeddingBatch(
            embeddings=tuple(vector for _ in range(max(count, 0))),
            model=self.response_model,
            dimension=self.response_dimension,
            latency_ms=1.25,
        )
        if self.mutate_model_after_call is not None:
            self.model_name = self.mutate_model_after_call
        return result

    async def aclose(self) -> None:
        self.closed = True


def service(
    provider: DeterministicProvider,
    *,
    batch_size: int = 2,
    normalize_vectors: bool = False,
    metric_sink: list[EmbeddingMetric] | None = None,
    **limits: int,
) -> EmbeddingService:
    return EmbeddingService(
        provider=provider,
        expected_dimension=provider.dimension,
        batch_size=batch_size,
        normalize_vectors=normalize_vectors,
        metric_sink=metric_sink.append if metric_sink is not None else None,
        **limits,
    )


def test_normalizes_text_splits_batches_and_emits_bounded_metric() -> None:
    provider = DeterministicProvider()
    metrics: list[EmbeddingMetric] = []

    result = run(
        service(provider, metric_sink=metrics).embed(
            ["  cafe\u0301\r\n", "two", " three ", "four", "five"]
        )
    )

    assert provider.calls == [("café", "two"), ("three", "four"), ("five",)]
    assert result.count == 5
    assert result.model == "test-embed"
    assert result.dimension == 3
    assert result.provider_calls == 3
    assert result.latency_ms >= 0
    assert metrics == [
        EmbeddingMetric(
            provider="deterministic",
            model="test-embed",
            input_count=5,
            provider_calls=3,
            dimension=3,
            latency_ms=metrics[0].latency_ms,
            normalized=False,
            outcome="ok",
        )
    ]


def test_closes_provider_through_service_boundary() -> None:
    provider = DeterministicProvider()

    run(service(provider).aclose())

    assert provider.closed is True


@pytest.mark.parametrize(
    ("texts", "message"),
    [
        (cast(Sequence[str], "one"), "sequence of strings"),
        ([], "at least one"),
        (["   "], "empty after normalization"),
        ([cast(str, 42)], "not a string"),
    ],
)
def test_rejects_invalid_inputs_without_provider_io(
    texts: Sequence[str],
    message: str,
) -> None:
    provider = DeterministicProvider()

    with pytest.raises(EmbeddingInputError, match=message):
        run(service(provider).embed(texts))

    assert provider.calls == []


def test_enforces_per_text_aggregate_and_item_count_limits() -> None:
    provider = DeterministicProvider()
    boundary = service(
        provider,
        max_inputs=2,
        max_text_characters=4,
        max_total_characters=6,
    )

    with pytest.raises(EmbeddingInputError, match="2-item"):
        run(boundary.embed(["a", "b", "c"]))
    with pytest.raises(EmbeddingInputError, match="4-character"):
        run(boundary.embed(["abcde"]))
    with pytest.raises(EmbeddingInputError, match="6-character aggregate"):
        run(boundary.embed(["abcd", "efg"]))

    assert provider.calls == []


@pytest.mark.parametrize(
    ("expected_dimension", "batch_size", "max_inputs", "message"),
    [
        (0, 1, 4_096, "expected_dimension"),
        (3, 0, 4_096, "batch_size"),
        (3, 129, 4_096, "at most 128"),
        (3, 1, 4_097, "at most 4096"),
    ],
)
def test_rejects_invalid_service_configuration(
    expected_dimension: int,
    batch_size: int,
    max_inputs: int,
    message: str,
) -> None:
    with pytest.raises(EmbeddingConfigurationError, match=message):
        EmbeddingService(
            provider=DeterministicProvider(),
            expected_dimension=expected_dimension,
            batch_size=batch_size,
            max_inputs=max_inputs,
        )


def test_total_character_limit_must_hold_one_maximum_sized_text() -> None:
    with pytest.raises(
        EmbeddingConfigurationError,
        match="max_total_characters must be at least max_text_characters",
    ):
        service(
            DeterministicProvider(),
            max_text_characters=101,
            max_total_characters=100,
        )


@pytest.mark.parametrize(
    ("field", "value", "maximum"),
    [
        ("max_text_characters", 32_001, 32_000),
        ("max_total_characters", 2_000_001, 2_000_000),
    ],
)
def test_service_character_limits_cannot_expand_past_reviewed_ceilings(
    field: str,
    value: int,
    maximum: int,
) -> None:
    with pytest.raises(EmbeddingConfigurationError, match=f"must be at most {maximum}"):
        service(DeterministicProvider(), **{field: value})


def test_rejects_model_switching_before_or_during_a_request() -> None:
    provider = DeterministicProvider()
    boundary = service(provider)
    provider.model_name = "changed-before-call"

    with pytest.raises(EmbeddingModelMismatchError) as before:
        run(boundary.embed(["one"]))
    assert (before.value.expected, before.value.actual) == (
        "test-embed",
        "changed-before-call",
    )
    assert provider.calls == []

    provider = DeterministicProvider()
    provider.mutate_model_after_call = "changed-during-call"
    with pytest.raises(EmbeddingModelMismatchError, match="changed-during-call"):
        run(service(provider).embed(["one"]))


def test_rejects_mismatched_response_model() -> None:
    provider = DeterministicProvider()
    provider.response_model = "unexpected-model"

    with pytest.raises(EmbeddingModelMismatchError) as caught:
        run(service(provider).embed(["one"]))

    assert caught.value.expected == "test-embed"
    assert caught.value.actual == "unexpected-model"


def test_rejects_response_count_and_dimension_mismatches() -> None:
    provider = DeterministicProvider()
    provider.response_count_delta = -1
    with pytest.raises(EmbeddingResponseError, match="response count"):
        run(service(provider).embed(["one"]))

    provider = DeterministicProvider()
    provider.response_dimension = 2
    with pytest.raises(EmbeddingDimensionError) as metadata:
        run(service(provider).embed(["one"]))
    assert (metadata.value.expected, metadata.value.actual, metadata.value.index) == (3, 2, 0)

    provider = DeterministicProvider()
    provider.vector_override = (1.0, 2.0)
    with pytest.raises(EmbeddingDimensionError) as vector:
        run(service(provider).embed(["one", "two", "three"]))
    assert vector.value.index == 0


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_components_and_emits_failure_metric(invalid: float) -> None:
    provider = DeterministicProvider()
    provider.vector_override = (1.0, invalid, 3.0)
    metrics: list[EmbeddingMetric] = []

    with pytest.raises(EmbeddingResponseError, match="non-finite"):
        run(service(provider, metric_sink=metrics).embed(["one"]))

    assert len(metrics) == 1
    assert metrics[0].outcome == "error"
    assert metrics[0].error_code == "embedding_invalid_response"
    assert metrics[0].input_count == 1
    assert metrics[0].provider_calls == 1


def test_optionally_l2_normalizes_vectors() -> None:
    provider = DeterministicProvider(dimension=2)
    provider.vector_override = (3.0, 4.0)

    result = run(service(provider, normalize_vectors=True).embed(["one"]))

    assert result.embeddings == ((0.6, 0.8),)
    assert result.normalized is True


def test_rejects_zero_vector_when_normalization_is_enabled() -> None:
    provider = DeterministicProvider(dimension=2)
    provider.vector_override = (0.0, 0.0)

    with pytest.raises(EmbeddingResponseError, match="cannot be L2-normalized"):
        run(service(provider, normalize_vectors=True).embed(["one"]))


def test_rejects_invalid_provider_latency_metadata() -> None:
    class InvalidLatencyProvider(DeterministicProvider):
        async def embed(
            self,
            texts: Sequence[str],
            *,
            expected_dimension: int,
        ) -> EmbeddingBatch:
            result = await super().embed(texts, expected_dimension=expected_dimension)
            return EmbeddingBatch(
                embeddings=result.embeddings,
                model=result.model,
                dimension=result.dimension,
                latency_ms=math.nan,
            )

    with pytest.raises(EmbeddingResponseError, match="latency metadata"):
        run(service(InvalidLatencyProvider()).embed(["one"]))
