from __future__ import annotations

from itertools import pairwise

import pytest

from mongars.memory.chunking import chunk_text


def _words(prefix: str, count: int) -> list[str]:
    return [f"{prefix}{index:03d}" for index in range(count)]


def test_long_paragraph_chunks_respect_bounds_and_overlap() -> None:
    source_words = _words("word", 83)
    chunks = chunk_text(" ".join(source_words), max_tokens=32, overlap_tokens=5)

    assert chunks
    assert all(1 <= chunk.approximate_tokens <= 32 for chunk in chunks)
    for previous, current in pairwise(chunks):
        assert previous.text.split()[-5:] == current.text.split()[:5]


def test_paragraph_transition_never_exceeds_chunk_bound() -> None:
    first = _words("first", 31)
    second = _words("second", 31)

    chunks = chunk_text(
        f"{' '.join(first)}\n\n{' '.join(second)}",
        max_tokens=32,
        overlap_tokens=10,
    )

    assert all(chunk.approximate_tokens <= 32 for chunk in chunks)


def test_chunking_is_deterministic_and_normalizes_whitespace() -> None:
    text = "  Alpha\t beta   gamma.\n\nDelta\n epsilon.  "

    first = chunk_text(text, max_tokens=32, overlap_tokens=4)
    second = chunk_text(text, max_tokens=32, overlap_tokens=4)

    assert first == second
    assert [chunk.text for chunk in first] == ["Alpha beta gamma. Delta epsilon."]
    assert first[0].approximate_tokens == 5


def test_empty_or_whitespace_only_text_has_no_chunks() -> None:
    assert chunk_text("") == []
    assert chunk_text(" \n\t \n") == []


def test_unbroken_input_is_split_at_the_character_ceiling_without_data_loss() -> None:
    text = "x" * 1_003

    chunks = chunk_text(
        text,
        max_tokens=32,
        overlap_tokens=0,
        max_characters=100,
    )

    assert all(1 <= len(chunk.text) <= 100 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == text


def test_character_ceiling_prefers_whitespace_boundaries() -> None:
    chunks = chunk_text(
        "alpha beta gamma delta epsilon",
        max_tokens=32,
        overlap_tokens=0,
        max_characters=12,
    )

    assert [chunk.text for chunk in chunks] == ["alpha beta", "gamma delta", "epsilon"]


@pytest.mark.parametrize(
    ("max_tokens", "overlap_tokens", "message"),
    [
        (31, 0, "max_tokens must be at least 32"),
        (32, -1, "overlap_tokens must be non-negative"),
        (32, 32, "overlap_tokens must be non-negative"),
        (32, 33, "overlap_tokens must be non-negative"),
    ],
)
def test_chunking_rejects_invalid_bounds(
    max_tokens: int,
    overlap_tokens: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        chunk_text("some text", max_tokens=max_tokens, overlap_tokens=overlap_tokens)


def test_chunking_rejects_non_positive_character_ceiling() -> None:
    with pytest.raises(ValueError, match="max_characters must be positive"):
        chunk_text("some text", max_characters=0)
