"""Markdown ingestion keeps source semantics while normalizing plain text safely."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mongars.ingestion.extractors.text import decode_utf8, enforce_section_limit, normalize_text
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent

_MARKDOWN_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S")
_MARKDOWN_BLOCK = re.compile(r"\n[\t ]*\n")


@dataclass(frozen=True, slots=True)
class MarkdownExtractor:
    media_type: DocumentMediaType = DocumentMediaType.MARKDOWN
    parser_name: str = "markdown-plaintext"
    parser_version: str = "1"

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        text = normalize_text(decode_utf8(content), max_chars=limits.max_extracted_chars)
        blocks = sum(1 for value in _MARKDOWN_BLOCK.split(text) if value.strip())
        headings = len(_MARKDOWN_HEADING.findall(text))
        section_count = max(1, blocks, headings)
        enforce_section_limit(section_count, limits)
        return ExtractedContent(
            text=text,
            page_count=None,
            section_count=section_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["MarkdownExtractor"]
