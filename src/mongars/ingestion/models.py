"""Typed, database-independent contracts for secure document extraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class DocumentMediaType(StrEnum):
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"
    HTML = "text/html"
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """Normalized parser output before common provenance is attached."""

    text: str
    page_count: int | None
    section_count: int | None
    parser_name: str
    parser_version: str


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
        }


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    text: str
    provenance: DocumentProvenance


__all__ = [
    "DocumentLimits",
    "DocumentMediaType",
    "DocumentProvenance",
    "ExtractedContent",
    "ExtractionResult",
    "IngestionContext",
    "UploadEnvelope",
    "ValidatedUpload",
]
