"""Format-specific, side-effect-free document extractors."""

from mongars.ingestion.extractors.docx import DocxExtractor
from mongars.ingestion.extractors.html import HtmlExtractor
from mongars.ingestion.extractors.markdown import MarkdownExtractor
from mongars.ingestion.extractors.pdf import PdfExtractor
from mongars.ingestion.extractors.text import TextExtractor

__all__ = [
    "DocxExtractor",
    "HtmlExtractor",
    "MarkdownExtractor",
    "PdfExtractor",
    "TextExtractor",
]
