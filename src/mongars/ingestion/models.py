"""Typed, database-independent contracts for secure document extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID


class DocumentMediaType(StrEnum):
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"
    HTML = "text/html"
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


_DOCUMENT_MEDIA_TYPES = frozenset(member.value for member in DocumentMediaType)
_CELL_REFERENCE = re.compile(r"^[A-Z]+[1-9][0-9]*$")
MAX_HEADING_COMPONENT_CHARACTERS = 500
MAX_HEADING_PATH_UTF8_BYTES = 2_048


@dataclass(frozen=True, slots=True)
class DocumentLocator:
    """Stable structural location for text extracted from one document.

    Page, line, and cell coordinates are one-based for display. ``block_index`` and
    ``table_index`` are zero-based because they identify ordered parser structures.
    """

    media_type: str
    block_index: int
    page_number: int | None = None
    heading_path: tuple[str, ...] = ()
    line_start: int | None = None
    line_end: int | None = None
    table_index: int | None = None
    cell_reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.media_type, str) or self.media_type not in _DOCUMENT_MEDIA_TYPES:
            raise ValueError("document locator media type is unsupported")
        if (
            isinstance(self.block_index, bool)
            or not isinstance(self.block_index, int)
            or self.block_index < 0
        ):
            raise ValueError("document locator block index must be non-negative")
        if self.page_number is not None and (
            isinstance(self.page_number, bool)
            or not isinstance(self.page_number, int)
            or self.page_number < 1
        ):
            raise ValueError("document locator page number must be positive")
        if not isinstance(self.heading_path, tuple) or len(self.heading_path) > 16:
            raise ValueError("document locator heading path is invalid")
        if any(
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or "\n" in value
            or "\r" in value
            or len(value) > MAX_HEADING_COMPONENT_CHARACTERS
            for value in self.heading_path
        ):
            raise ValueError("document locator heading path is invalid")
        if (
            sum(len(value.encode("utf-8")) for value in self.heading_path)
            > MAX_HEADING_PATH_UTF8_BYTES
        ):
            raise ValueError("document locator heading path is too large")
        if (self.line_start is None) is not (self.line_end is None):
            raise ValueError("document locator line range must include both endpoints")
        if self.line_start is not None and self.line_end is not None:
            if (
                isinstance(self.line_start, bool)
                or isinstance(self.line_end, bool)
                or not isinstance(self.line_start, int)
                or not isinstance(self.line_end, int)
                or self.line_start < 1
                or self.line_end < self.line_start
            ):
                raise ValueError("document locator line range is invalid")
        if self.table_index is not None and (
            isinstance(self.table_index, bool)
            or not isinstance(self.table_index, int)
            or self.table_index < 0
        ):
            raise ValueError("document locator table index must be non-negative")
        if self.cell_reference is not None and (
            not isinstance(self.cell_reference, str)
            or not self.cell_reference
            or self.cell_reference != self.cell_reference.strip()
            or len(self.cell_reference) > 32
            or _CELL_REFERENCE.fullmatch(self.cell_reference) is None
        ):
            raise ValueError("document locator cell reference is invalid")
        if self.cell_reference is not None and self.table_index is None:
            raise ValueError("document locator cell reference requires a table index")

    def validate_for_document(
        self,
        *,
        media_type: DocumentMediaType,
        page_count: int | None,
        maximum_blocks: int,
    ) -> None:
        """Reject coordinates that are impossible for the parsed media type."""

        if self.media_type != media_type.value:
            raise ValueError("document locator media type does not match the document")
        if self.block_index >= maximum_blocks:
            raise ValueError("document locator block index exceeds its limit")
        if self.table_index is not None and self.table_index >= maximum_blocks:
            raise ValueError("document locator table index exceeds its limit")

        has_lines = self.line_start is not None
        has_table = self.table_index is not None
        if media_type is DocumentMediaType.PDF:
            if (
                self.page_number is None
                or page_count is None
                or self.page_number > page_count
                or has_lines
                or has_table
                or self.heading_path
            ):
                raise ValueError("PDF segment locator is inconsistent with the document")
            return
        if self.page_number is not None:
            raise ValueError("non-PDF segment locator must not contain a page number")
        if media_type is DocumentMediaType.TEXT:
            if not has_lines or self.heading_path or has_table:
                raise ValueError("text segment locator is inconsistent with the document")
            return
        if media_type is DocumentMediaType.MARKDOWN:
            if not has_lines or has_table:
                raise ValueError("Markdown segment locator is inconsistent with the document")
            return
        if has_lines:
            raise ValueError("HTML and DOCX locators must not contain source line ranges")
        if media_type not in {DocumentMediaType.HTML, DocumentMediaType.DOCX}:
            raise ValueError("document segment locator media type is unsupported")
        if has_table is not (self.cell_reference is not None):
            raise ValueError("table segment locator must identify one exact cell")

    def as_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible parser-boundary representation."""

        return {
            "media_type": self.media_type,
            "page_number": self.page_number,
            "heading_path": list(self.heading_path),
            "block_index": self.block_index,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "table_index": self.table_index,
            "cell_reference": self.cell_reference,
        }

    @classmethod
    def from_dict(cls, value: object) -> DocumentLocator:
        """Decode a strict parser-boundary representation without coercion."""

        if not isinstance(value, dict):
            raise TypeError("document locator must be an object")
        expected_keys = {
            "media_type",
            "page_number",
            "heading_path",
            "block_index",
            "line_start",
            "line_end",
            "table_index",
            "cell_reference",
        }
        if set(value) != expected_keys:
            raise ValueError("document locator has an invalid shape")
        media_type = value["media_type"]
        block_index = value["block_index"]
        heading_path = value["heading_path"]
        if not isinstance(media_type, str):
            raise TypeError("document locator media type must be a string")
        if isinstance(block_index, bool) or not isinstance(block_index, int):
            raise TypeError("document locator block index must be an integer")
        if not isinstance(heading_path, list) or not all(
            isinstance(item, str) for item in heading_path
        ):
            raise TypeError("document locator heading path must be a string array")
        optional_integers: dict[str, int | None] = {}
        for key in ("page_number", "line_start", "line_end", "table_index"):
            item = value[key]
            if item is not None and (isinstance(item, bool) or not isinstance(item, int)):
                raise TypeError(f"document locator {key} must be an integer or null")
            optional_integers[key] = item
        cell_reference = value["cell_reference"]
        if cell_reference is not None and not isinstance(cell_reference, str):
            raise TypeError("document locator cell reference must be a string or null")
        return cls(
            media_type=media_type,
            page_number=optional_integers["page_number"],
            heading_path=tuple(heading_path),
            block_index=block_index,
            line_start=optional_integers["line_start"],
            line_end=optional_integers["line_end"],
            table_index=optional_integers["table_index"],
            cell_reference=cell_reference,
        )


@dataclass(frozen=True, slots=True)
class ExtractedSegment:
    """Normalized text paired with the exact structure it came from."""

    text: str
    locator: DocumentLocator

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not self.text
            or not self.text.strip()
            or self.text != self.text.strip()
        ):
            raise ValueError("extracted segment text must be non-empty and trimmed")
        if not isinstance(self.locator, DocumentLocator):
            raise TypeError("extracted segment locator must be a DocumentLocator")


@dataclass(frozen=True, slots=True)
class LocatedTextChunk:
    """A bounded embedding chunk that cannot lose its source locator."""

    text: str
    approximate_tokens: int
    locator: DocumentLocator

    def __post_init__(self) -> None:
        if (
            not isinstance(self.text, str)
            or not self.text
            or not self.text.strip()
            or self.text != self.text.strip()
        ):
            raise ValueError("located chunk text must be non-empty and trimmed")
        if (
            isinstance(self.approximate_tokens, bool)
            or not isinstance(self.approximate_tokens, int)
            or self.approximate_tokens < 1
        ):
            raise ValueError("located chunk token estimate must be positive")
        if not isinstance(self.locator, DocumentLocator):
            raise TypeError("located chunk locator must be a DocumentLocator")


@dataclass(frozen=True, slots=True)
class DocumentLimits:
    """All parser limits are explicit and can only be tightened by a caller."""

    max_input_bytes: int = 10_000_000
    max_extracted_chars: int = 2_000_000
    max_pages: int = 500
    max_sections: int = 10_000
    max_archive_members: int = 2_000
    max_archive_uncompressed_bytes: int = 50_000_000
    max_archive_member_bytes: int = 20_000_000
    max_compression_ratio: float = 100.0

    def __post_init__(self) -> None:
        integer_limits = (
            self.max_input_bytes,
            self.max_extracted_chars,
            self.max_pages,
            self.max_sections,
            self.max_archive_members,
            self.max_archive_uncompressed_bytes,
            self.max_archive_member_bytes,
        )
        if any(value <= 0 for value in integer_limits):
            raise ValueError("document limits must be positive")
        if self.max_compression_ratio < 1:
            raise ValueError("max_compression_ratio must be at least 1")


@dataclass(frozen=True, slots=True)
class UploadEnvelope:
    """An upload represented only by metadata and immutable bytes, never a path."""

    original_filename: str
    declared_mime_type: str
    content: bytes = field(repr=False)
    declared_size: int | None = None


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    """Envelope validation result suitable for bounded staging."""

    original_filename: str
    validated_mime_type: DocumentMediaType
    content_sha256: str
    byte_size: int
    content: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class IngestionContext:
    """Governance and provenance supplied by the approved durable task."""

    owner_id: str
    ingestion_task_id: UUID
    sensitivity: str
    retention_class: str
    source_timestamp: datetime
    received_at: datetime
    source_time_basis: Literal["user_supplied"] = "user_supplied"


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """Normalized parser output before common provenance is attached."""

    text: str
    segments: tuple[ExtractedSegment, ...]
    page_count: int | None
    section_count: int | None
    parser_name: str
    parser_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or not self.segments:
            raise ValueError("extracted content must contain structured segments")
        if not all(isinstance(segment, ExtractedSegment) for segment in self.segments):
            raise TypeError("extracted content segments must be ExtractedSegment values")
        if self.text != "\n\n".join(segment.text for segment in self.segments):
            raise ValueError("extracted content text must be the canonical segment projection")


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    sha256: str
    original_filename: str
    validated_mime_type: str
    byte_size: int
    extracted_character_count: int
    page_count: int | None
    section_count: int | None
    parser_name: str
    parser_version: str
    ingestion_task_id: UUID
    owner_id: str
    sensitivity: str
    retention_class: str
    source_timestamp: datetime
    received_at: datetime
    source_time_basis: Literal["user_supplied"]

    def as_metadata(self) -> dict[str, Any]:
        """Return JSON-compatible metadata for Hippocampus provenance."""

        return {
            "sha256": self.sha256,
            "original_filename": self.original_filename,
            "validated_mime_type": self.validated_mime_type,
            "byte_size": self.byte_size,
            "extracted_character_count": self.extracted_character_count,
            "page_count": self.page_count,
            "section_count": self.section_count,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "ingestion_task_id": str(self.ingestion_task_id),
            "owner_id": self.owner_id,
            "sensitivity": self.sensitivity,
            "retention_class": self.retention_class,
            "source_timestamp": self.source_timestamp.isoformat(),
            "received_at": self.received_at.isoformat(),
            "source_time_basis": self.source_time_basis,
        }


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str
    segments: tuple[ExtractedSegment, ...]
    provenance: DocumentProvenance


__all__ = [
    "MAX_HEADING_COMPONENT_CHARACTERS",
    "MAX_HEADING_PATH_UTF8_BYTES",
    "DocumentLimits",
    "DocumentLocator",
    "DocumentMediaType",
    "DocumentProvenance",
    "ExtractedContent",
    "ExtractedSegment",
    "ExtractionResult",
    "IngestionContext",
    "LocatedTextChunk",
    "UploadEnvelope",
    "ValidatedUpload",
]
