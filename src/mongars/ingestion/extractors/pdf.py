"""Bounded born-digital PDF extraction; OCR is intentionally out of scope."""

from __future__ import annotations

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
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent


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

        extracted_pages: list[str] = []
        extracted_characters = 0
        try:
            for page in reader.pages:
                page_text = page.extract_text() or ""
                extracted_characters += len(page_text)
                if extracted_characters > limits.max_extracted_chars:
                    raise ExtractedTextTooLargeError(
                        "extracted text exceeds the configured character limit"
                    )
                if normalized := page_text.strip():
                    extracted_pages.append(normalized)
        except ExtractedTextTooLargeError:
            raise
        except (PdfReadError, OSError, TypeError, ValueError, KeyError) as exc:
            raise MalformedDocumentError("PDF text content is malformed") from exc

        text = normalize_text(
            "\n\n".join(extracted_pages),
            max_chars=limits.max_extracted_chars,
        )
        return ExtractedContent(
            text=text,
            page_count=page_count,
            section_count=len(extracted_pages),
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["PdfExtractor"]
