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
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent

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

    if any(character in _FORBIDDEN_CONTROLS for character in value):
        raise MalformedDocumentError("document text contains binary control characters")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _MULTIPLE_BLANK_LINES.sub("\n\n", normalized).strip()
    if len(normalized) > max_chars:
        raise ExtractedTextTooLargeError("extracted text exceeds the configured character limit")
    if not normalized or not any(not character.isspace() for character in normalized):
        raise NoUsableTextError("document contains no usable text")
    return normalized


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
        text = normalize_text(decode_utf8(content), max_chars=limits.max_extracted_chars)
        section_count = count_text_sections(text)
        enforce_section_limit(section_count, limits)
        return ExtractedContent(
            text=text,
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
    "normalize_text",
]
