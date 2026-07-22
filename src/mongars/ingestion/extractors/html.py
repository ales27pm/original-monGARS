"""Fail-closed HTML sanitization and text extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from bs4 import BeautifulSoup, Comment, ProcessingInstruction, Tag
from bs4.element import NavigableString

from mongars.ingestion.errors import (
    DocumentStructureLimitError,
    MalformedDocumentError,
    UnsafeDocumentError,
)
from mongars.ingestion.extractors.structure import HeadingPathTracker, cell_reference
from mongars.ingestion.extractors.text import decode_utf8, enforce_section_limit, normalize_text
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentLocator,
    DocumentMediaType,
    ExtractedContent,
    ExtractedSegment,
)

_ACTIVE_TAGS = frozenset(
    {
        "applet",
        "base",
        "embed",
        "form",
        "frame",
        "frameset",
        "iframe",
        "math",
        "object",
        "script",
        "svg",
    }
)
_NON_CONTENT_TAGS = frozenset({"head", "link", "meta", "noscript", "style", "template"})
_ATOMIC_CONTENT_TAGS = frozenset(
    {
        "address",
        "blockquote",
        "dd",
        "dt",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "pre",
    }
)
_CONTENT_CONTAINERS = frozenset(
    {
        "article",
        "aside",
        "body",
        "div",
        "dl",
        "figure",
        "footer",
        "header",
        "html",
        "main",
        "nav",
        "ol",
        "section",
        "ul",
    }
)
_DANGEROUS_URL = re.compile(
    r"^\s*(?:(?:javascript|vbscript)\s*:|data\s*:\s*text/html)",
    re.I,
)
_EXTERNAL_DOCTYPE = re.compile(r"<!doctype[^>]+\b(?:system|public)\b", re.I)
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?:\.0*)?)\s*(?:;|$)",
    re.I,
)
_URL_ATTRIBUTES = frozenset(
    {"action", "background", "cite", "data", "formaction", "href", "poster", "src", "xlink:href"}
)


def _package_version() -> str:
    try:
        return version("beautifulsoup4")
    except PackageNotFoundError:
        return "unknown"


def _reject_active_content(soup: BeautifulSoup, raw_text: str) -> None:
    if _EXTERNAL_DOCTYPE.search(raw_text):
        raise UnsafeDocumentError("HTML contains an external document type reference")
    if soup.find(list(_ACTIVE_TAGS)) is not None:
        raise UnsafeDocumentError("HTML contains active content")
    if soup.find("style") is not None:
        raise UnsafeDocumentError("HTML contains a stylesheet that cannot be evaluated safely")

    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        if element.has_attr("class") or element.has_attr("id"):
            raise UnsafeDocumentError(
                "HTML contains class or ID selectors that cannot be evaluated safely"
            )
        if element.name == "link":
            raw_relationships = element.get("rel")
            relationships: list[str]
            if isinstance(raw_relationships, str):
                relationships = raw_relationships.split()
            elif isinstance(raw_relationships, list):
                relationships = [str(value) for value in raw_relationships]
            else:
                relationships = []
            if any(str(value).casefold() == "stylesheet" for value in relationships):
                raise UnsafeDocumentError(
                    "HTML contains a stylesheet link that cannot be evaluated safely"
                )
        for raw_name, raw_value in element.attrs.items():
            name = raw_name.casefold()
            if name.startswith("on") or name in {"srcdoc", "ping"}:
                raise UnsafeDocumentError("HTML contains an active attribute")
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            if name in _URL_ATTRIBUTES and any(
                _DANGEROUS_URL.match(str(value)) for value in values
            ):
                raise UnsafeDocumentError("HTML contains an active URL")
        if element.name == "meta" and str(element.get("http-equiv", "")).casefold() == "refresh":
            raise UnsafeDocumentError("HTML contains an active refresh directive")


def _remove_hidden_content(soup: BeautifulSoup) -> None:
    for node in soup.find_all(
        string=lambda value: isinstance(value, (Comment, ProcessingInstruction))
    ):
        node.extract()
    for element in list(soup.find_all(list(_NON_CONTENT_TAGS))):
        element.decompose()
    hidden_elements: list[Tag] = []
    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        hidden = (
            element.has_attr("hidden") or str(element.get("aria-hidden", "")).casefold() == "true"
        )
        inline_style = str(element.get("style", ""))
        if hidden or _HIDDEN_STYLE.search(inline_style):
            hidden_elements.append(element)
    for element in hidden_elements:
        element.decompose()
    for element in soup.find_all(True):
        if isinstance(element, Tag) and element.has_attr("style"):
            # CSS can load resources and has enough parser ambiguity that accepting
            # selected declarations would not be a useful security boundary.
            raise UnsafeDocumentError("HTML contains active inline styling")


def _structured_segments(
    soup: BeautifulSoup,
    *,
    media_type: DocumentMediaType,
    max_chars: int,
    max_sections: int,
) -> tuple[ExtractedSegment, ...]:
    root = soup.body or soup
    tracker = HeadingPathTracker()
    segments: list[ExtractedSegment] = []
    next_table_index = 0

    def append_segment(
        raw_text: str,
        *,
        heading_path: tuple[str, ...],
        table_index: int | None = None,
        cell: str | None = None,
    ) -> None:
        if not raw_text.strip():
            return
        if len(segments) >= max_sections:
            raise DocumentStructureLimitError("HTML exceeds the configured section limit")
        segments.append(
            ExtractedSegment(
                text=normalize_text(raw_text, max_chars=max_chars),
                locator=DocumentLocator(
                    media_type=media_type.value,
                    block_index=len(segments),
                    heading_path=heading_path,
                    table_index=table_index,
                    cell_reference=cell,
                ),
            )
        )

    def walk(container: Tag | BeautifulSoup) -> None:
        nonlocal next_table_index
        inline_parts: list[str] = []

        def flush_inline() -> None:
            if inline_parts:
                append_segment(" ".join(inline_parts), heading_path=tracker.current)
                inline_parts.clear()

        for child in container.children:
            if isinstance(child, NavigableString):
                if value := str(child).strip():
                    inline_parts.append(value)
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name.casefold()
            if name == "table":
                flush_inline()
                table_index = next_table_index
                next_table_index += 1
                rows = [
                    row
                    for row in child.find_all("tr")
                    if isinstance(row, Tag) and row.find_parent("table") is child
                ]
                occupied_columns: dict[int, int] = {}
                for row_index, row in enumerate(rows):
                    occupied_columns = {
                        column: remaining - 1
                        for column, remaining in occupied_columns.items()
                        if remaining > 1
                    }
                    cells = row.find_all(["th", "td"], recursive=False)
                    next_column = 0
                    for table_cell in cells:
                        if isinstance(table_cell, Tag):
                            while next_column in occupied_columns:
                                next_column += 1
                            column_span = _table_span(
                                table_cell,
                                "colspan",
                                maximum=max_sections,
                            )
                            row_span = _table_span(
                                table_cell,
                                "rowspan",
                                maximum=max_sections,
                                zero_value=len(rows) - row_index,
                            )
                            append_segment(
                                table_cell.get_text(" ", strip=True),
                                heading_path=tracker.current,
                                table_index=table_index,
                                cell=cell_reference(row_index, next_column),
                            )
                            for column in range(next_column, next_column + column_span):
                                occupied_columns[column] = max(
                                    occupied_columns.get(column, 0),
                                    row_span,
                                )
                            next_column += column_span
                continue
            if name in _ATOMIC_CONTENT_TAGS:
                flush_inline()
                value = child.get_text(" ", strip=True)
                heading_path = tracker.current
                if len(name) == 2 and name[0] == "h" and name[1].isdigit() and value:
                    heading_path = tracker.update(int(name[1]), value)
                append_segment(value, heading_path=heading_path)
                continue
            if name in _CONTENT_CONTAINERS:
                flush_inline()
                walk(child)
                continue
            if value := child.get_text(" ", strip=True):
                inline_parts.append(value)
        flush_inline()

    walk(root)

    if not segments:
        append_segment(root.get_text("\n", strip=True), heading_path=())
    return tuple(segments)


def _table_span(
    cell: Tag,
    attribute: str,
    *,
    maximum: int,
    zero_value: int | None = None,
) -> int:
    raw_value = cell.get(attribute)
    if raw_value is None:
        return 1
    value = str(raw_value).strip()
    if not value.isascii() or not value.isdecimal():
        raise MalformedDocumentError(f"HTML table {attribute} is invalid")
    span = int(value)
    if span == 0 and zero_value is not None:
        span = zero_value
    if span < 1:
        raise MalformedDocumentError(f"HTML table {attribute} is invalid")
    if span > maximum:
        raise DocumentStructureLimitError(f"HTML table {attribute} exceeds its limit")
    return span


@dataclass(frozen=True, slots=True)
class HtmlExtractor:
    media_type: DocumentMediaType = DocumentMediaType.HTML
    parser_name: str = "beautifulsoup-html"
    parser_version: str = _package_version()

    def extract(self, content: bytes, *, limits: DocumentLimits) -> ExtractedContent:
        raw_text = decode_utf8(content)
        soup = BeautifulSoup(raw_text, "html.parser")
        _reject_active_content(soup, raw_text)
        _remove_hidden_content(soup)

        segments = _structured_segments(
            soup,
            media_type=self.media_type,
            max_chars=limits.max_extracted_chars,
            max_sections=limits.max_sections,
        )
        section_count = len(segments)
        enforce_section_limit(section_count, limits)
        text = normalize_text(
            "\n\n".join(segment.text for segment in segments),
            max_chars=limits.max_extracted_chars,
        )
        return ExtractedContent(
            text=text,
            segments=segments,
            page_count=None,
            section_count=section_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["HtmlExtractor"]
