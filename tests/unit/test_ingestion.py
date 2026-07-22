from __future__ import annotations

import stat
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest
from docx import Document
from pypdf import PdfWriter

from mongars.ingestion import (
    ContentTypeMismatchError,
    DocumentIngestionService,
    DocumentLimits,
    DocumentStructureLimitError,
    DocumentTooLargeError,
    EncryptedDocumentError,
    ExtractedTextTooLargeError,
    IngestionContext,
    InvalidFilenameError,
    IsolatedDocumentParser,
    MalformedDocumentError,
    NoUsableTextError,
    ParserProcessLimits,
    ParserTimeoutError,
    UnsafeDocumentError,
    UnsupportedDocumentTypeError,
    UploadEnvelope,
)
from mongars.ingestion.extractors.text import TextExtractor
from mongars.ingestion.registry import ExtractorRegistry

_TASK_ID = UUID("018f0a1e-8d46-7a3e-83c6-75ba32f83601")


def _context() -> IngestionContext:
    return IngestionContext(
        owner_id="owner-a",
        ingestion_task_id=_TASK_ID,
        sensitivity="restricted",
        retention_class="ttl_30d",
        source_timestamp=datetime(2026, 7, 22, 12, 30, tzinfo=UTC),
    )


def _extract(
    filename: str,
    mime_type: str,
    content: bytes,
    *,
    limits: DocumentLimits | None = None,
):
    service = DocumentIngestionService(limits=limits)
    validated = service.validate_envelope(
        UploadEnvelope(
            original_filename=filename,
            declared_mime_type=mime_type,
            declared_size=len(content),
            content=content,
        )
    )
    return service.extract_validated(validated, context=_context())


def _make_pdf(text: str = "Hello from PDF") -> bytes:
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
    ]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects.extend(
        (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        )
    )
    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(value)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def _make_docx() -> bytes:
    document = Document()
    document.add_heading("Safety report", level=1)
    document.add_paragraph("A bounded DOCX body.")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "monGARS"
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _rewrite_docx(
    content: bytes,
    *,
    replacements: dict[str, bytes] | None = None,
    additions: tuple[tuple[ZipInfo | str, bytes], ...] = (),
) -> bytes:
    replacements = replacements or {}
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for member in source.infolist():
            value = replacements.get(member.filename, source.read(member.filename))
            target.writestr(member, value)
        for name, value in additions:
            target.writestr(name, value)
    return output.getvalue()


def test_txt_extraction_returns_bounded_provenance() -> None:
    result = _extract("notes.txt", "text/plain; charset=utf-8", b"First\r\n\r\nSecond\n")

    assert result.text == "First\n\nSecond"
    assert result.provenance.owner_id == "owner-a"
    assert result.provenance.ingestion_task_id == _TASK_ID
    assert result.provenance.validated_mime_type == "text/plain"
    assert result.provenance.byte_size == 16
    assert result.provenance.extracted_character_count == len(result.text)
    assert result.provenance.section_count == 2
    assert result.provenance.parser_name == "utf8-text"
    assert result.provenance.as_metadata()["source_timestamp"] == "2026-07-22T12:30:00+00:00"


def test_markdown_extraction_preserves_source_semantics() -> None:
    result = _extract(
        "design.MD",
        "text/x-markdown",
        b"# Cortex\n\n- bounded context\n- explicit policy\n",
    )

    assert result.text.startswith("# Cortex")
    assert "- explicit policy" in result.text
    assert result.provenance.validated_mime_type == "text/markdown"


def test_html_sanitization_preserves_visible_text_and_removes_known_hidden_text() -> None:
    result = _extract(
        "page.html",
        "text/html",
        b"""<!doctype html><html><head><title>Not body content</title></head><body>
        <!-- secret --><main><h1>Visible</h1><p hidden>Hidden one</p>
        <p style="display: none">Hidden two</p><p>Useful body</p></main></body></html>""",
    )

    assert "Visible" in result.text
    assert "Useful body" in result.text
    assert "secret" not in result.text
    assert "Hidden" not in result.text
    assert "Not body content" not in result.text


def test_html_rejects_stylesheet_hidden_content_ambiguity() -> None:
    content = (
        b"<!doctype html><html><head><style>.hidden{display:none}</style></head>"
        b"<body><p class=hidden>IGNORE</p></body></html>"
    )

    with pytest.raises(UnsafeDocumentError, match="stylesheet"):
        _extract("page.html", "text/html", content)


@pytest.mark.parametrize(
    "fragment",
    [
        '<link rel="stylesheet" href="theme.css"><p>text</p>',
        '<p class="possibly-hidden">text</p>',
        '<p id="possibly-hidden">text</p>',
    ],
)
def test_html_rejects_css_selector_ambiguity(fragment: str) -> None:
    content = f"<!doctype html><html><body>{fragment}</body></html>".encode()
    with pytest.raises(UnsafeDocumentError, match=r"stylesheet|selectors"):
        _extract("page.html", "text/html", content)


@pytest.mark.parametrize(
    "fragment",
    [
        '<script src="https://evil.invalid/x.js"></script>',
        '<form action="https://evil.invalid"><input></form>',
        '<iframe src="https://evil.invalid"></iframe>',
        '<svg><a href="https://evil.invalid">x</a></svg>',
        "<math><mi>x</mi></math>",
        '<p onclick="steal()">click</p>',
        '<a href="javascript:steal()">click</a>',
        '<a href="data:text/html,attack">click</a>',
        '<meta http-equiv="refresh" content="0;https://evil.invalid">',
        '<p style="color: red">styled</p>',
    ],
)
def test_html_rejects_active_content(fragment: str) -> None:
    content = f"<!doctype html><html><body><p>safe</p>{fragment}</body></html>".encode()

    with pytest.raises(UnsafeDocumentError):
        _extract("page.html", "text/html", content)


def test_html_rejects_external_doctype() -> None:
    content = b'<!DOCTYPE html SYSTEM "file:///etc/passwd"><html><body>safe</body></html>'

    with pytest.raises(UnsafeDocumentError, match="document type"):
        _extract("page.html", "text/html", content)


def test_pdf_extraction_and_page_provenance() -> None:
    result = _extract("report.pdf", "application/pdf", _make_pdf())

    assert result.text == "Hello from PDF"
    assert result.provenance.page_count == 1
    assert result.provenance.section_count == 1
    assert result.provenance.parser_name == "pypdf"


def test_pdf_rejects_encryption_even_when_password_is_empty() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("")
    output = BytesIO()
    writer.write(output)

    with pytest.raises(EncryptedDocumentError):
        _extract("secret.pdf", "application/pdf", output.getvalue())


def test_pdf_rejects_excessive_pages_before_extraction() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)

    with pytest.raises(DocumentStructureLimitError, match="page limit"):
        _extract(
            "pages.pdf",
            "application/pdf",
            output.getvalue(),
            limits=DocumentLimits(max_pages=1),
        )


def test_pdf_without_text_is_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)

    with pytest.raises(NoUsableTextError):
        _extract("blank.pdf", "application/pdf", output.getvalue())


def test_docx_extracts_body_and_table() -> None:
    result = _extract(
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _make_docx(),
    )

    assert "Safety report" in result.text
    assert "A bounded DOCX body." in result.text
    assert "Name\tmonGARS" in result.text
    assert result.provenance.parser_name == "python-docx"
    assert result.provenance.section_count == 3


def test_docx_rejects_external_relationship() -> None:
    original = _make_docx()
    with ZipFile(BytesIO(original)) as archive:
        relationships = archive.read("word/_rels/document.xml.rels")
    injected = relationships.replace(
        b"</Relationships>",
        (
            b'<Relationship Id="evil" Type="urn:evil" '
            b'Target="file:///etc/passwd" TargetMode="External"/></Relationships>'
        ),
    )
    malicious = _rewrite_docx(
        original,
        replacements={"word/_rels/document.xml.rels": injected},
    )

    with pytest.raises(UnsafeDocumentError, match="external relationship"):
        _extract(
            "external.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            malicious,
        )


@pytest.mark.parametrize("member_name", ["../escape.txt", "/absolute.txt", "C:/host.txt"])
def test_docx_rejects_unsafe_archive_paths(member_name: str) -> None:
    malicious = _rewrite_docx(_make_docx(), additions=((member_name, b"attack"),))

    with pytest.raises(UnsafeDocumentError):
        _extract(
            "unsafe.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            malicious,
        )


def test_docx_rejects_symbolic_link_member() -> None:
    symlink = ZipInfo("word/link")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    malicious = _rewrite_docx(_make_docx(), additions=((symlink, b"/etc/passwd"),))

    with pytest.raises(UnsafeDocumentError, match="symbolic link"):
        _extract(
            "unsafe.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            malicious,
        )


def test_docx_rejects_decompression_bomb_metadata() -> None:
    malicious = _rewrite_docx(
        _make_docx(),
        additions=(("word/bomb.txt", b"A" * 1_000_000),),
    )

    with pytest.raises(DocumentStructureLimitError, match="compression ratio"):
        _extract(
            "bomb.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            malicious,
            limits=DocumentLimits(max_compression_ratio=100),
        )


def test_docx_rejects_truncated_archive() -> None:
    truncated = _make_docx()[:-32]

    with pytest.raises(MalformedDocumentError, match="malformed or truncated"):
        _extract(
            "truncated.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            truncated,
        )


@pytest.mark.parametrize(
    "filename",
    [
        "../note.txt",
        "/note.txt",
        "folder\\note.txt",
        "bad\x00.txt",
        "invoice\u202efdp.txt",
        "invisible\u200b.txt",
    ],
)
def test_filename_must_be_a_safe_basename(filename: str) -> None:
    service = DocumentIngestionService()

    with pytest.raises(InvalidFilenameError):
        service.validate_envelope(UploadEnvelope(filename, "text/plain", b"safe"))


def test_filename_is_nfc_normalized_and_confusable_separators_are_rejected() -> None:
    service = DocumentIngestionService()
    validated = service.validate_envelope(
        UploadEnvelope("re\u0301sume\u0301.txt", "text/plain", b"safe")
    )

    assert validated.original_filename == "résumé.txt"
    with pytest.raises(InvalidFilenameError):
        service.validate_envelope(UploadEnvelope("folder\u2215note.txt", "text/plain", b"safe"))


def test_unsupported_extension_and_mime_are_rejected() -> None:
    service = DocumentIngestionService()

    with pytest.raises(UnsupportedDocumentTypeError):
        service.validate_envelope(UploadEnvelope("archive.zip", "application/zip", b"PK"))
    with pytest.raises(UnsupportedDocumentTypeError):
        service.validate_envelope(UploadEnvelope("note.txt", "application/octet-stream", b"safe"))


@pytest.mark.parametrize(
    ("filename", "mime_type", "content"),
    [
        ("report.pdf", "text/plain", _make_pdf()),
        ("report.txt", "text/plain", _make_pdf()),
        ("report.pdf", "application/pdf", b"plain UTF-8 text"),
        ("page.html", "text/html", b"plain UTF-8 text"),
        ("page.txt", "text/plain", b"<html><body>HTML</body></html>"),
    ],
)
def test_mime_extension_and_bytes_must_agree(
    filename: str,
    mime_type: str,
    content: bytes,
) -> None:
    with pytest.raises(ContentTypeMismatchError):
        _extract(filename, mime_type, content)


def test_declared_and_streamed_size_must_agree() -> None:
    service = DocumentIngestionService()

    with pytest.raises(MalformedDocumentError, match="size"):
        service.validate_envelope(
            UploadEnvelope("note.txt", "text/plain", b"hello", declared_size=500)
        )


def test_input_and_extracted_character_limits_are_independent() -> None:
    with pytest.raises(DocumentTooLargeError):
        _extract(
            "note.txt",
            "text/plain",
            b"0123456789",
            limits=DocumentLimits(max_input_bytes=5),
        )
    with pytest.raises(ExtractedTextTooLargeError):
        _extract(
            "note.txt",
            "text/plain",
            b"0123456789",
            limits=DocumentLimits(max_extracted_chars=5),
        )


def test_empty_binary_and_excessive_sections_are_rejected() -> None:
    with pytest.raises(NoUsableTextError):
        _extract("empty.txt", "text/plain", b"  \n\n ")
    with pytest.raises(MalformedDocumentError, match="control"):
        _extract("binary.txt", "text/plain", b"safe\x01unsafe")
    with pytest.raises(DocumentStructureLimitError, match="section"):
        _extract(
            "sections.txt",
            "text/plain",
            b"one\n\ntwo",
            limits=DocumentLimits(max_sections=1),
        )


def test_staged_bytes_are_revalidated_before_parser_dispatch() -> None:
    service = DocumentIngestionService()
    validated = service.validate_envelope(UploadEnvelope("note.txt", "text/plain", b"original"))
    tampered = replace(validated, content=b"attacker")

    with pytest.raises(UnsafeDocumentError, match="integrity"):
        service.extract_validated(tampered, context=_context())


def test_context_requires_governance_and_timezone() -> None:
    service = DocumentIngestionService()
    validated = service.validate_envelope(UploadEnvelope("note.txt", "text/plain", b"safe"))

    with pytest.raises(ValueError, match="sensitivity"):
        service.extract_validated(validated, context=replace(_context(), sensitivity="public"))
    with pytest.raises(ValueError, match="timezone-aware"):
        service.extract_validated(
            validated,
            context=replace(_context(), source_timestamp=datetime(2026, 7, 22)),
        )


def test_registry_rejects_duplicate_media_type() -> None:
    with pytest.raises(ValueError, match="already registered"):
        ExtractorRegistry((TextExtractor(), TextExtractor()))


@pytest.mark.asyncio
async def test_isolated_parser_returns_result_from_disposable_subprocess() -> None:
    service = DocumentIngestionService()
    validated = service.validate_envelope(UploadEnvelope("note.txt", "text/plain", b"isolated"))

    result = await IsolatedDocumentParser().extract(validated)

    assert result.text == "isolated"
    assert result.parser_name == "utf8-text"


@pytest.mark.asyncio
async def test_isolated_parser_enforces_wall_clock_timeout() -> None:
    service = DocumentIngestionService()
    validated = service.validate_envelope(UploadEnvelope("note.txt", "text/plain", b"isolated"))
    parser = IsolatedDocumentParser(process_limits=ParserProcessLimits(timeout_seconds=0.000_001))

    with pytest.raises(ParserTimeoutError):
        await parser.extract(validated)
