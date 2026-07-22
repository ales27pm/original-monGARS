"""Locator-preserving chunking for structured document extraction."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from mongars.ingestion.models import ExtractedSegment, LocatedTextChunk

_WHITESPACE = re.compile(r"\s+")


def chunk_segments(
    segments: Sequence[ExtractedSegment],
    *,
    max_tokens: int = 800,
    overlap_tokens: int = 100,
    max_characters: int = 32_000,
) -> tuple[LocatedTextChunk, ...]:
    """Chunk each structural segment independently and retain its locator.

    Segments are deliberately never combined. This prevents a chunk from crossing a
    page, heading, table cell, or source-line boundary established by the extractor.
    """

    if max_tokens < 32:
        raise ValueError("max_tokens must be at least 32")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be non-negative and smaller than max_tokens")
    if max_characters < 1:
        raise ValueError("max_characters must be positive")

    chunks: list[LocatedTextChunk] = []
    for source_segment in segments:
        candidates: tuple[ExtractedSegment, ...] = (source_segment,)
        source_words = source_segment.text.split()
        if len(source_words) > max_tokens or len(source_segment.text) > max_characters:
            candidates = _split_source_lines(source_segment)
        for segment in candidates:
            chunks.extend(
                _chunk_one_segment(
                    segment,
                    max_tokens=max_tokens,
                    overlap_tokens=overlap_tokens,
                    max_characters=max_characters,
                )
            )
    return tuple(chunks)


def _chunk_one_segment(
    segment: ExtractedSegment,
    *,
    max_tokens: int,
    overlap_tokens: int,
    max_characters: int,
) -> list[LocatedTextChunk]:
    chunks: list[LocatedTextChunk] = []
    words = segment.text.split()
    if len(words) <= max_tokens and len(segment.text) <= max_characters:
        return [
            LocatedTextChunk(
                text=segment.text,
                approximate_tokens=len(words),
                locator=segment.locator,
            )
        ]

    for word_window in _word_windows(words, max_tokens, overlap_tokens):
        value = " ".join(word_window)
        for character_window in _character_windows(value, max_characters):
            chunks.append(
                LocatedTextChunk(
                    text=character_window,
                    approximate_tokens=len(character_window.split()),
                    locator=segment.locator,
                )
            )
    return chunks


def _split_source_lines(segment: ExtractedSegment) -> tuple[ExtractedSegment, ...]:
    locator = segment.locator
    if locator.line_start is None or locator.line_end is None or "\n" not in segment.text:
        return (segment,)
    lines = segment.text.splitlines()
    if len(lines) > locator.line_end - locator.line_start + 1:
        raise ValueError("segment text contains more lines than its source locator")
    split_segments: list[ExtractedSegment] = []
    for offset, line in enumerate(lines):
        if not line.strip():
            continue
        line_number = locator.line_start + offset
        split_segments.append(
            ExtractedSegment(
                text=line.strip(),
                locator=replace(
                    locator,
                    line_start=line_number,
                    line_end=line_number,
                ),
            )
        )
    return tuple(split_segments)


def _word_windows(words: list[str], maximum: int, overlap: int) -> list[list[str]]:
    windows: list[list[str]] = []
    start = 0
    while start < len(words):
        end = min(start + maximum, len(words))
        windows.append(words[start:end])
        if end == len(words):
            break
        start = end - overlap
    return windows


def _character_windows(value: str, maximum: int) -> tuple[str, ...]:
    windows: list[str] = []
    remaining = value
    while len(remaining) > maximum:
        boundary = remaining.rfind(" ", 0, maximum + 1)
        if boundary <= 0:
            boundary = maximum
        window = _WHITESPACE.sub(" ", remaining[:boundary]).strip()
        if window:
            windows.append(window)
        remaining = remaining[boundary:].strip()
    if remaining:
        windows.append(_WHITESPACE.sub(" ", remaining).strip())
    return tuple(windows)


__all__ = ["chunk_segments"]
