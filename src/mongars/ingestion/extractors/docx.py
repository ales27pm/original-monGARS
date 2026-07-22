"""Bounded DOCX package validation and plain-text extraction."""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError
from docx import Document
from docx.opc.exceptions import PackageNotFoundError as DocxPackageNotFoundError
from docx.table import Table
from docx.text.paragraph import Paragraph

from mongars.ingestion.errors import (
    DocumentStructureLimitError,
    MalformedDocumentError,
    UnsafeDocumentError,
)
from mongars.ingestion.extractors.text import normalize_text
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent

_CONTENT_TYPES_PART = "[Content_Types].xml"
_DOCUMENT_PART = "word/document.xml"
_REQUIRED_PARTS = frozenset({_CONTENT_TYPES_PART, _DOCUMENT_PART})
_DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_DRIVE_PATH = re.compile(r"^[a-zA-Z]:")


def _package_version() -> str:
    try:
        return version("python-docx")
    except PackageNotFoundError:
        return "unknown"


def _validate_member_name(name: str) -> None:
    if not name or "\x00" in name or "\\" in name or _DRIVE_PATH.match(name):
        raise UnsafeDocumentError("DOCX archive contains an unsafe member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UnsafeDocumentError("DOCX archive contains a path traversal member")


def _validate_relationships(archive: ZipFile, relationship_parts: list[str]) -> None:
    for part_name in relationship_parts:
        try:
            root = ElementTree.fromstring(archive.read(part_name))
        except (DefusedXmlException, ParseError, KeyError, OSError, RuntimeError) as exc:
            raise MalformedDocumentError("DOCX contains malformed relationships") from exc
        for relationship in root.iter():
            if relationship.tag.rsplit("}", 1)[-1] != "Relationship":
                continue
            target_mode = relationship.attrib.get("TargetMode", "").casefold()
            target = relationship.attrib.get("Target", "").strip()
            parsed_target = urlsplit(target)
            external_path = (
                parsed_target.scheme != ""
                or parsed_target.netloc != ""
                or target.startswith(("/", "\\"))
                or _DRIVE_PATH.match(target) is not None
            )
            if target_mode == "external" or external_path:
                raise UnsafeDocumentError("DOCX contains an external relationship")


def inspect_docx_archive(content: bytes, *, limits: DocumentLimits) -> None:
    """Validate package structure before python-docx is allowed to open it."""

    try:
        archive = ZipFile(BytesIO(content))
    except (BadZipFile, OSError, ValueError) as exc:
        raise MalformedDocumentError("DOCX archive is malformed or truncated") from exc

    with archive:
        members = archive.infolist()
        if len(members) > limits.max_archive_members:
            raise DocumentStructureLimitError("DOCX exceeds the configured archive member limit")

        seen_names: set[str] = set()
        total_uncompressed = 0
        for member in members:
            _validate_member_name(member.filename)
            if member.filename in seen_names:
                raise MalformedDocumentError("DOCX archive contains duplicate member names")
            seen_names.add(member.filename)
            if member.flag_bits & 0x1:
                raise UnsafeDocumentError("encrypted DOCX archive members are not supported")
            unix_mode = member.external_attr >> 16
            if stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise UnsafeDocumentError("DOCX archive contains a symbolic link")
            if member.file_size > limits.max_archive_member_bytes:
                raise DocumentStructureLimitError(
                    "DOCX contains an archive member that exceeds the configured limit"
                )
            total_uncompressed += member.file_size
            if total_uncompressed > limits.max_archive_uncompressed_bytes:
                raise DocumentStructureLimitError(
                    "DOCX exceeds the configured uncompressed archive limit"
                )
            if member.file_size:
                if member.compress_size <= 0:
                    raise DocumentStructureLimitError("DOCX contains an invalid compressed member")
                ratio = member.file_size / member.compress_size
                if ratio > limits.max_compression_ratio:
                    raise DocumentStructureLimitError(
                        "DOCX contains a suspicious compression ratio"
                    )

        if not _REQUIRED_PARTS.issubset(seen_names):
            raise MalformedDocumentError("DOCX is missing required package parts")

        try:
            content_types = ElementTree.fromstring(archive.read(_CONTENT_TYPES_PART))
        except (DefusedXmlException, ParseError, KeyError, OSError, RuntimeError) as exc:
            raise MalformedDocumentError("DOCX content types are malformed") from exc
        declared_document_part = any(
            element.attrib.get("PartName") == "/word/document.xml"
            and element.attrib.get("ContentType") == _DOCX_CONTENT_TYPE
            for element in content_types.iter()
            if element.tag.rsplit("}", 1)[-1] == "Override"
        )
        if not declared_document_part:
            raise MalformedDocumentError("ZIP package is not a DOCX document")

        relationship_parts = [name for name in seen_names if name.casefold().endswith(".rels")]
        _validate_relationships(archive, relationship_parts)
        try:
            corrupt_member = archive.testzip()
        except (BadZipFile, OSError, RuntimeError) as exc:
            raise MalformedDocumentError("DOCX archive is malformed or truncated") from exc
        if corrupt_member is not None:
            raise MalformedDocumentError("DOCX archive contains corrupt data")


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            rows.append("\t".join(cells))
    return "\n".join(rows)


@dataclass(frozen=True, slots=True)
class DocxExtractor:
    media_type: DocumentMediaType = DocumentMediaType.DOCX
    parser_name: str = "python-docx"
    parser_version: str = _package_version()

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        inspect_docx_archive(content, limits=limits)
        try:
            document = Document(BytesIO(content))
        except (BadZipFile, DocxPackageNotFoundError, KeyError, OSError, ValueError) as exc:
            raise MalformedDocumentError("DOCX package cannot be opened") from exc

        blocks: list[str] = []
        try:
            for block in document.iter_inner_content():
                if isinstance(block, Paragraph):
                    value = block.text.strip()
                elif isinstance(block, Table):
                    value = _table_text(block)
                else:  # pragma: no cover - protects against future python-docx block types
                    continue
                if value:
                    blocks.append(value)
                    if len(blocks) > limits.max_sections:
                        raise DocumentStructureLimitError(
                            "DOCX exceeds the configured section limit"
                        )
        except DocumentStructureLimitError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise MalformedDocumentError("DOCX body content is malformed") from exc

        text = normalize_text("\n\n".join(blocks), max_chars=limits.max_extracted_chars)
        return ExtractedContent(
            text=text,
            page_count=None,
            section_count=len(blocks),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["DocxExtractor", "inspect_docx_archive"]
