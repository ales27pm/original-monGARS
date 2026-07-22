"""UTF-8 text extraction and common normalization helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from mongars.ingestion.errors import (
    DocumentStructureLimitError,
    ExtractedTextTooLargeError,
    MalformedDocumentError,
    NoUsableTextError,
)
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentLocator,
    DocumentMediaType,
    ExtractedContent,
    ExtractedSegment,
)

_MULTIPLE_BLANK_LINES = re.compile(r"\n[\t ]*\n(?:[\t ]*\n)+")
_PARAGRAPH_BREAK = re.compile(r"\n[\t ]*\n")
_FORBIDDEN_CONTROLS = frozenset(
    chr(code) for code in (*range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)
)


def decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise MalformedDocumentError("text documents must use valid UTF-8") from exc


def normalize_text(value: str, *, max_chars: int) -> str:
    """Normalize line endings and Unicode without hiding binary control bytes."""

    normalized = "\n".join(normalize_source_lines(value))
    normalized = _MULTIPLE_BLANK_LINES.sub("\n\n", normalized).strip()
    if len(normalized) > max_chars:
        raise ExtractedTextTooLargeError("extracted text exceeds the configured character limit")
    if not normalized or not any(not character.isspace() for character in normalized):
        raise NoUsableTextError("document contains no usable text")
    return normalized


def normalize_source_lines(value: str) -> list[str]:
    """Return normalized source lines while retaining their one-based positions."""

    if any(character in _FORBIDDEN_CONTROLS for character in value):
        raise MalformedDocumentError("document text contains binary control characters")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    return [line.rstrip() for line in normalized.split("\n")]


def line_segments(
    value: str,
    *,
    media_type: DocumentMediaType,
    max_chars: int,
) -> tuple[str, tuple[ExtractedSegment, ...]]:
    """Extract nonblank line blocks with stable one-based line ranges."""

    text = normalize_text(value, max_chars=max_chars)
    lines = normalize_source_lines(value)
    segments: list[ExtractedSegment] = []
    block_start: int | None = None
    for offset in range(len(lines) + 1):
        line = lines[offset] if offset < len(lines) else ""
        if line.strip() and block_start is None:
            block_start = offset
        if (not line.strip() or offset == len(lines)) and block_start is not None:
            block_end = offset - 1 if not line.strip() else offset
            block_text = normalize_text(
                "\n".join(lines[block_start : block_end + 1]),
                max_chars=max_chars,
            )
            segments.append(
                ExtractedSegment(
                    text=block_text,
                    locator=DocumentLocator(
                        media_type=media_type.value,
                        block_index=len(segments),
                        line_start=block_start + 1,
                        line_end=block_end + 1,
                    ),
                )
            )
            block_start = None
    return text, tuple(segments)


def count_text_sections(value: str) -> int:
    return sum(1 for section in _PARAGRAPH_BREAK.split(value) if section.strip())


def enforce_section_limit(section_count: int, limits: DocumentLimits) -> None:
    if section_count > limits.max_sections:
        raise DocumentStructureLimitError("document exceeds the configured section limit")


@dataclass(frozen=True, slots=True)
class TextExtractor:
    media_type: DocumentMediaType = DocumentMediaType.TEXT
    parser_name: str = "utf8-text"
    parser_version: str = "1"

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        text, segments = line_segments(
            decode_utf8(content),
            media_type=self.media_type,
            max_chars=limits.max_extracted_chars,
        )
        section_count = len(segments)
        enforce_section_limit(section_count, limits)
        return ExtractedContent(
            text=text,
            segments=segments,
            page_count=None,
            section_count=section_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = [
    "TextExtractor",
    "count_text_sections",
    "decode_utf8",
    "enforce_section_limit",
    "line_segments",
    "normalize_source_lines",
    "normalize_text",
]
