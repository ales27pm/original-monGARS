"""Typed registry for the deliberately small document parser allowlist."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from mongars.ingestion.errors import UnsupportedDocumentTypeError
from mongars.ingestion.extractors import (
    DocxExtractor,
    HtmlExtractor,
    MarkdownExtractor,
    PdfExtractor,
    TextExtractor,
)
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent


@runtime_checkable
class DocumentExtractor(Protocol):
    @property
    def media_type(self) -> DocumentMediaType: ...

    @property
    def parser_name(self) -> str: ...

    @property
    def parser_version(self) -> str: ...

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent: ...


class ExtractorRegistry:
    def __init__(self, extractors: Iterable[DocumentExtractor] = ()) -> None:
        self._extractors: dict[DocumentMediaType, DocumentExtractor] = {}
        for extractor in extractors:
            self.register(extractor)

    def register(self, extractor: DocumentExtractor) -> None:
        if not isinstance(extractor, DocumentExtractor):
            raise TypeError("extractor does not implement the document extractor contract")
        if extractor.media_type in self._extractors:
            raise ValueError(f"an extractor is already registered for {extractor.media_type}")
        self._extractors[extractor.media_type] = extractor

    def get(self, media_type: DocumentMediaType) -> DocumentExtractor:
        try:
            return self._extractors[media_type]
        except KeyError as exc:
            raise UnsupportedDocumentTypeError(
                "no parser is registered for this document type"
            ) from exc


def default_extractor_registry() -> ExtractorRegistry:
    return ExtractorRegistry(
        (
            TextExtractor(),
            MarkdownExtractor(),
            HtmlExtractor(),
            PdfExtractor(),
            DocxExtractor(),
        )
    )


__all__ = ["DocumentExtractor", "ExtractorRegistry", "default_extractor_registry"]
