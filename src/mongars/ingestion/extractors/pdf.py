"""Bounded born-digital PDF extraction; OCR is intentionally out of scope."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from mongars.ingestion.errors import (
    DocumentStructureLimitError,
    EncryptedDocumentError,
    ExtractedTextTooLargeError,
    MalformedDocumentError,
)
from mongars.ingestion.extractors.text import normalize_text
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentLocator,
    DocumentMediaType,
    ExtractedContent,
    ExtractedSegment,
)

_BLOCK_BREAK = re.compile(r"\n\s*\n+")


def _package_version() -> str:
    try:
        return version("pypdf")
    except PackageNotFoundError:
        return "unknown"


@dataclass(frozen=True, slots=True)
class PdfExtractor:
    media_type: DocumentMediaType = DocumentMediaType.PDF
    parser_name: str = "pypdf"
    parser_version: str = _package_version()

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        try:
            reader = PdfReader(BytesIO(content), strict=True)
        except (PdfReadError, OSError, ValueError) as exc:
            raise MalformedDocumentError("PDF is malformed or truncated") from exc

        if reader.is_encrypted:
            raise EncryptedDocumentError("encrypted PDF documents are not supported")

        try:
            page_count = len(reader.pages)
        except (PdfReadError, OSError, ValueError) as exc:
            raise MalformedDocumentError("PDF page tree is malformed") from exc
        if page_count > limits.max_pages:
            raise DocumentStructureLimitError("PDF exceeds the configured page limit")

        segments: list[ExtractedSegment] = []
        extracted_characters = 0
        try:
            for page_number, page in enumerate(reader.pages, 1):
                page_text = page.extract_text() or ""
                extracted_characters += len(page_text)
                if extracted_characters > limits.max_extracted_chars:
                    raise ExtractedTextTooLargeError(
                        "extracted text exceeds the configured character limit"
                    )
                if page_text.strip():
                    normalized_page = normalize_text(
                        page_text,
                        max_chars=limits.max_extracted_chars,
                    )
                    for block_index, block in enumerate(_BLOCK_BREAK.split(normalized_page)):
                        if not block.strip():
                            continue
                        segments.append(
                            ExtractedSegment(
                                text=block.strip(),
                                locator=DocumentLocator(
                                    media_type=self.media_type.value,
                                    page_number=page_number,
                                    block_index=block_index,
                                ),
                            )
                        )
                        if len(segments) > limits.max_sections:
                            raise DocumentStructureLimitError(
                                "PDF exceeds the configured section limit"
                            )
        except ExtractedTextTooLargeError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError, KeyError) as exc:
            raise MalformedDocumentError("PDF text content is malformed") from exc

        text = normalize_text(
            "\n\n".join(segment.text for segment in segments),
            max_chars=limits.max_extracted_chars,
        )
        return ExtractedContent(
            text=text,
            segments=tuple(segments),
            page_count=page_count,
            section_count=len(segments),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["PdfExtractor"]
