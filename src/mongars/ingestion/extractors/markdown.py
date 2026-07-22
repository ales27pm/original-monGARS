"""Markdown ingestion keeps source semantics while normalizing plain text safely."""

from __future__ import annotations

import re
from dataclasses import dataclass

from mongars.ingestion.extractors.structure import HeadingPathTracker
from mongars.ingestion.extractors.text import (
    decode_utf8,
    enforce_section_limit,
    normalize_source_lines,
    normalize_text,
)
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentLocator,
    DocumentMediaType,
    ExtractedContent,
    ExtractedSegment,
)

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}(#{1,6})[\t ]+(.+?)(?:[\t ]+#+[\t ]*)?$")
_SETEXT_HEADING = re.compile(r"^\s{0,3}(=+|-+)\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_CLOSING_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})[\t ]*$")


def _blocks(lines: list[str]) -> list[tuple[int, int, str]]:
    blocks: list[tuple[int, int, str]] = []
    current: list[str] = []
    current_start = 0
    fence_character: str | None = None
    fence_length = 0

    def flush(end: int) -> None:
        nonlocal current
        if current:
            blocks.append((current_start, end, "\n".join(current)))
            current = []

    for index, line in enumerate(lines):
        heading = _MARKDOWN_HEADING.match(line) if fence_character is None else None
        setext_heading = _SETEXT_HEADING.match(line) if fence_character is None else None
        fence = _FENCE.match(line)
        if heading is not None:
            flush(index - 1)
            blocks.append((index, index, line))
            continue
        if setext_heading is not None and current:
            current.append(line)
            flush(index)
            continue
        if fence is not None:
            marker = fence.group(1)
            if fence_character is None:
                flush(index - 1)
                current_start = index
                fence_character = marker[0]
                fence_length = len(marker)
                current.append(line)
            elif (
                marker[0] == fence_character
                and len(marker) >= fence_length
                and _CLOSING_FENCE.fullmatch(line) is not None
            ):
                current.append(line)
                fence_character = None
                fence_length = 0
                flush(index)
            else:
                current.append(line)
            continue
        if not current and line.strip():
            current_start = index
        if not line.strip() and fence_character is None:
            flush(index - 1)
            continue
        if line.strip() or current:
            current.append(line)
    flush(len(lines) - 1)
    return blocks


@dataclass(frozen=True, slots=True)
class MarkdownExtractor:
    media_type: DocumentMediaType = DocumentMediaType.MARKDOWN
    parser_name: str = "markdown-plaintext"
    parser_version: str = "1"

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        decoded = decode_utf8(content)
        normalize_text(decoded, max_chars=limits.max_extracted_chars)
        tracker = HeadingPathTracker()
        segments: list[ExtractedSegment] = []
        for line_start, line_end, raw_block in _blocks(normalize_source_lines(decoded)):
            block_text = normalize_text(raw_block, max_chars=limits.max_extracted_chars)
            heading = _MARKDOWN_HEADING.match(block_text)
            heading_path = tracker.current
            if heading is not None:
                heading_path = tracker.update(len(heading.group(1)), heading.group(2).strip())
            else:
                block_lines = block_text.splitlines()
                if len(block_lines) == 2 and (underline := _SETEXT_HEADING.match(block_lines[1])):
                    level = 1 if underline.group(1).startswith("=") else 2
                    heading_path = tracker.update(level, block_lines[0])
            segments.append(
                ExtractedSegment(
                    text=block_text,
                    locator=DocumentLocator(
                        media_type=self.media_type.value,
                        block_index=len(segments),
                        heading_path=heading_path,
                        line_start=line_start + 1,
                        line_end=line_end + 1,
                    ),
                )
            )
        section_count = len(segments)
        enforce_section_limit(section_count, limits)
        text = normalize_text(
            "\n\n".join(segment.text for segment in segments),
            max_chars=limits.max_extracted_chars,
        )
        return ExtractedContent(
            text=text,
            segments=tuple(segments),
            page_count=None,
            section_count=section_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["MarkdownExtractor"]
