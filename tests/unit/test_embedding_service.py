from __future__ import annotations

import asyncio
import math
from collections.abc import Coroutine, Sequence
from typing import Any, cast

import pytest

from mongars.embeddings import (
    EmbeddingBatch,
    EmbeddingConfigurationError,
    EmbeddingContextError,
    EmbeddingDimensionError,
    EmbeddingInputError,
    EmbeddingMetric,
    EmbeddingModelMismatchError,
    EmbeddingPurpose,
    EmbeddingResponseError,
    EmbeddingService,
)

_MODEL_DIGEST = "1" * 64


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
        self.model_digest = _MODEL_DIGEST
        self.response_digest = _MODEL_DIGEST
        self.response_count_delta = 0
        self.vector_override: tuple[float, ...] | None = None
        self.mutate_model_after_call: str | None = None
        self.closed = False

    async def resolve_model_digest(self) -> str:
        return self.model_digest

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
            model_digest=self.response_digest,
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


def embed(
    boundary: EmbeddingService,
    texts: Sequence[str],
    *,
    purpose: EmbeddingPurpose = "search_query",
) -> Coroutine[Any, Any, EmbeddingBatch]:
    return boundary.embed(texts, purpose=purpose)


def test_normalizes_text_splits_batches_and_emits_bounded_metric() -> None:
    provider = DeterministicProvider()
    metrics: list[EmbeddingMetric] = []

    result = run(
        embed(
            service(provider, metric_sink=metrics),
            ["  cafe\u0301\r\n", "two", " three ", "four", "five"],
        )
    )

    assert provider.calls == [
        ("search_query: café", "search_query: two"),
        ("search_query: three", "search_query: four"),
        ("search_query: five",),
    ]
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
            purpose="search_query",
            embedding_space_id=result.embedding_space_id,
            input_bytes=sum(len(text.encode()) for call in provider.calls for text in call),
        )
    ]


@pytest.mark.parametrize(
    ("purpose", "prefix"),
    [
        ("search_document", "search_document: "),
        ("search_query", "search_query: "),
        ("clustering", "clustering: "),
        ("classification", "classification: "),
    ],
)
def test_service_owns_the_reviewed_purpose_prefix_policy(
    purpose: EmbeddingPurpose,
    prefix: str,
) -> None:
    provider = DeterministicProvider()
    boundary = service(provider)

    result = run(embed(boundary, ["do not alter"], purpose=purpose))

    assert provider.calls == [(f"{prefix}do not alter",)]
    assert result.purpose == purpose
    assert result.model_digest == _MODEL_DIGEST
    assert boundary.embedding_space is not None
    assert result.embedding_space_id == boundary.embedding_space.space_id


def test_rejects_unknown_purpose_without_provider_io() -> None:
    provider = DeterministicProvider()

    with pytest.raises(EmbeddingInputError, match="Unsupported embedding purpose"):
        run(service(provider).embed(["one"], purpose=cast(Any, "unknown")))

    assert provider.calls == []


def test_enforces_utf8_byte_ceiling_after_adding_the_instruction() -> None:
    provider = DeterministicProvider()
    boundary = service(provider, max_text_bytes=32, max_total_bytes=64)

    with pytest.raises(EmbeddingContextError) as caught:
        run(embed(boundary, ["é" * 10]))

    assert caught.value.input_index == 0
    assert caught.value.input_bytes == len(("search_query: " + ("é" * 10)).encode())
    assert caught.value.maximum_input_bytes == 32
    assert provider.calls == []


def test_metric_sink_failure_is_nonfatal() -> None:
    provider = DeterministicProvider()

    def broken_sink(_metric: EmbeddingMetric) -> None:
        raise RuntimeError("metrics unavailable")

    boundary = EmbeddingService(
        provider=provider,
        expected_dimension=provider.dimension,
        batch_size=2,
        metric_sink=broken_sink,
    )

    result = run(embed(boundary, ["one"]))

    assert result.count == 1


def test_missing_resolved_space_fails_as_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = DeterministicProvider()
    metrics: list[EmbeddingMetric] = []
    boundary = service(provider, metric_sink=metrics)

    async def missing_space() -> None:
        return None

    monkeypatch.setattr(boundary, "resolve_space", missing_space)

    with pytest.raises(
        EmbeddingConfigurationError,
        match="resolution returned no identity",
    ):
        run(embed(boundary, ["one"]))

    assert provider.calls == []
    assert metrics[0].outcome == "error"
    assert metrics[0].error_code == "embedding_configuration_error"


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
        run(embed(service(provider), texts))

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
        run(embed(boundary, ["a", "b", "c"]))
    with pytest.raises(EmbeddingInputError, match="4-character"):
        run(embed(boundary, ["abcde"]))
    with pytest.raises(EmbeddingInputError, match="6-character aggregate"):
        run(embed(boundary, ["abcd", "efg"]))

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
        run(embed(boundary, ["one"]))
    assert (before.value.expected, before.value.actual) == (
        "test-embed",
        "changed-before-call",
    )
    assert provider.calls == []

    provider = DeterministicProvider()
    provider.mutate_model_after_call = "changed-during-call"
    with pytest.raises(EmbeddingModelMismatchError, match="changed-during-call"):
        run(embed(service(provider), ["one"]))


def test_rejects_mismatched_response_model() -> None:
    provider = DeterministicProvider()
    provider.response_model = "unexpected-model"

    with pytest.raises(EmbeddingModelMismatchError) as caught:
        run(embed(service(provider), ["one"]))

    assert caught.value.expected == "test-embed"
    assert caught.value.actual == "unexpected-model"


def test_rejects_provider_artifact_drift() -> None:
    provider = DeterministicProvider()
    boundary = service(provider)

    first = run(embed(boundary, ["one"]))
    provider.model_digest = "2" * 64

    with pytest.raises(EmbeddingResponseError, match="unexpected artifact digest"):
        run(embed(boundary, ["two"]))

    assert first.model_digest == _MODEL_DIGEST


def test_rejects_batch_from_an_artifact_other_than_the_resolved_model() -> None:
    provider = DeterministicProvider()
    provider.response_digest = "2" * 64

    with pytest.raises(EmbeddingResponseError, match="unexpected artifact digest"):
        run(embed(service(provider), ["one"]))


def test_rejects_response_count_and_dimension_mismatches() -> None:
    provider = DeterministicProvider()
    provider.response_count_delta = -1
    with pytest.raises(EmbeddingResponseError, match="response count"):
        run(embed(service(provider), ["one"]))

    provider = DeterministicProvider()
    provider.response_dimension = 2
    with pytest.raises(EmbeddingDimensionError) as metadata:
        run(embed(service(provider), ["one"]))
    assert (metadata.value.expected, metadata.value.actual, metadata.value.index) == (3, 2, 0)

    provider = DeterministicProvider()
    provider.vector_override = (1.0, 2.0)
    with pytest.raises(EmbeddingDimensionError) as vector:
        run(embed(service(provider), ["one", "two", "three"]))
    assert vector.value.index == 0


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_components_and_emits_failure_metric(invalid: float) -> None:
    provider = DeterministicProvider()
    provider.vector_override = (1.0, invalid, 3.0)
    metrics: list[EmbeddingMetric] = []

    with pytest.raises(EmbeddingResponseError, match="non-finite"):
        run(embed(service(provider, metric_sink=metrics), ["one"]))

    assert len(metrics) == 1
    assert metrics[0].outcome == "error"
    assert metrics[0].error_code == "embedding_invalid_response"
    assert metrics[0].input_count == 1
    assert metrics[0].provider_calls == 1


def test_optionally_l2_normalizes_vectors() -> None:
    provider = DeterministicProvider(dimension=2)
    provider.vector_override = (3.0, 4.0)

    result = run(embed(service(provider, normalize_vectors=True), ["one"]))

    assert result.embeddings == ((0.6, 0.8),)
    assert result.normalized is True


def test_rejects_zero_vector_when_normalization_is_enabled() -> None:
    provider = DeterministicProvider(dimension=2)
    provider.vector_override = (0.0, 0.0)

    with pytest.raises(EmbeddingResponseError, match="cannot be L2-normalized"):
        run(embed(service(provider, normalize_vectors=True), ["one"]))


def test_rejects_zero_vector_for_cosine_retrieval_without_normalization() -> None:
    provider = DeterministicProvider(dimension=2)
    provider.vector_override = (0.0, -0.0)
    metrics: list[EmbeddingMetric] = []

    with pytest.raises(EmbeddingResponseError, match="zero magnitude"):
        run(embed(service(provider, normalize_vectors=False, metric_sink=metrics), ["one"]))

    assert metrics[0].outcome == "error"
    assert metrics[0].error_code == "embedding_invalid_response"


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
                model_digest=result.model_digest,
                dimension=result.dimension,
                latency_ms=math.nan,
            )

    with pytest.raises(EmbeddingResponseError, match="latency metadata"):
        run(embed(service(InvalidLatencyProvider()), ["one"]))
