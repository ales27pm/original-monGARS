"""Fail-closed HTML sanitization and text extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from bs4 import BeautifulSoup, Comment, ProcessingInstruction, Tag

from mongars.ingestion.errors import UnsafeDocumentError
from mongars.ingestion.extractors.text import decode_utf8, enforce_section_limit, normalize_text
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ExtractedContent

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
_SECTION_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "pre",
        "section",
        "table",
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

        section_count = sum(
            1
            for element in soup.find_all(list(_SECTION_TAGS))
            if isinstance(element, Tag) and element.get_text(" ", strip=True)
        )
        section_count = max(1, section_count)
        enforce_section_limit(section_count, limits)
        text = normalize_text(soup.get_text("\n"), max_chars=limits.max_extracted_chars)
        return ExtractedContent(
            text=text,
            page_count=None,
            section_count=section_count,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )


__all__ = ["HtmlExtractor"]
