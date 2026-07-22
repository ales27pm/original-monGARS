from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    text: str
    approximate_tokens: int
    section_path: tuple[str, ...] = ()


_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")
_WHITESPACE = re.compile(r"\s+")


def _normalize_paragraph(value: str) -> str:
    return _WHITESPACE.sub(" ", value).strip()


def _word_windows(words: list[str], max_words: int, overlap_words: int) -> list[list[str]]:
    windows: list[list[str]] = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        windows.append(words[start:end])
        if end == len(words):
            break
        start = end - overlap_words
    return windows


def chunk_text(
    text: str,
    *,
    max_tokens: int = 800,
    overlap_tokens: int = 100,
    max_characters: int = 32_000,
) -> list[TextChunk]:
    """Create deterministic, paragraph-aware chunks without a model-specific tokenizer.

    Word count is used as a conservative token estimate. Exact token counts remain the
    embedding backend's concern.
    """

    if max_tokens < 32:
        raise ValueError("max_tokens must be at least 32")
    if overlap_tokens < 0 or overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be non-negative and smaller than max_tokens")
    if max_characters < 1:
        raise ValueError("max_characters must be positive")

    paragraphs = [
        normalized
        for raw in _PARAGRAPH_BREAK.split(text.strip())
        if (normalized := _normalize_paragraph(raw))
    ]
    if not paragraphs:
        return []

    chunks: list[list[str]] = []
    current: list[str] = []

    for paragraph in paragraphs:
        words = paragraph.split()
        if len(words) > max_tokens:
            if current:
                chunks.append(current)
                current = []
            chunks.extend(_word_windows(words, max_tokens, overlap_tokens))
            continue

        if current and len(current) + len(words) > max_tokens:
            chunks.append(current)
            available_overlap = min(overlap_tokens, max_tokens - len(words))
            overlap = current[-available_overlap:] if available_overlap else []
            current = [*overlap, *words]
        else:
            current.extend(words)

    if current:
        chunks.append(current)

    bounded: list[TextChunk] = []
    for words in chunks:
        if not words:
            continue
        for value in _character_windows(" ".join(words), max_characters):
            bounded.append(
                TextChunk(
                    text=value,
                    approximate_tokens=len(value.split()),
                )
            )
    return bounded


def _character_windows(value: str, max_characters: int) -> list[str]:
    """Split oversized text at whitespace when possible, including unbroken strings."""

    windows: list[str] = []
    remaining = value
    while len(remaining) > max_characters:
        boundary = remaining.rfind(" ", 0, max_characters + 1)
        if boundary <= 0:
            boundary = max_characters
        window = remaining[:boundary].strip()
        if window:
            windows.append(window)
        remaining = remaining[boundary:].strip()
    if remaining:
        windows.append(remaining)
    return windows
