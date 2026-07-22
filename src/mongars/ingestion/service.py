"""Pure document envelope validation and extraction orchestration."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import PurePosixPath

from mongars.ingestion.errors import (
    ContentTypeMismatchError,
    DocumentStructureLimitError,
    DocumentTooLargeError,
    InvalidFilenameError,
    MalformedDocumentError,
    UnsafeDocumentError,
    UnsupportedDocumentTypeError,
)
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentMediaType,
    DocumentProvenance,
    ExtractedContent,
    ExtractionResult,
    IngestionContext,
    UploadEnvelope,
    ValidatedUpload,
)
from mongars.ingestion.registry import ExtractorRegistry, default_extractor_registry

_EXTENSION_MEDIA_TYPES: dict[str, DocumentMediaType] = {
    ".txt": DocumentMediaType.TEXT,
    ".md": DocumentMediaType.MARKDOWN,
    ".markdown": DocumentMediaType.MARKDOWN,
    ".html": DocumentMediaType.HTML,
    ".htm": DocumentMediaType.HTML,
    ".pdf": DocumentMediaType.PDF,
    ".docx": DocumentMediaType.DOCX,
}
_MIME_ALIASES: dict[str, DocumentMediaType] = {
    "text/plain": DocumentMediaType.TEXT,
    "text/markdown": DocumentMediaType.MARKDOWN,
    "text/x-markdown": DocumentMediaType.MARKDOWN,
    "text/html": DocumentMediaType.HTML,
    "application/pdf": DocumentMediaType.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        DocumentMediaType.DOCX
    ),
}
_HTML_SIGNATURE = re.compile(
    r"^\s*(?:<!doctype\s+html\b[^>]*>|<!--.*?-->\s*)?\s*"
    r"<(?:html|head|body|title|meta|article|section|main|div|p|h[1-6]|ul|ol|table)\b",
    re.I | re.S,
)
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_FORBIDDEN_FILENAME_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")
_CONFUSABLE_PATH_SEPARATORS = frozenset(
    {"\u2044", "\u2215", "\u29f5", "\u29f8", "\uff0f", "\uff3c"}
)


class DocumentIngestionService:
    """Validate upload bytes and dispatch only to allowlisted extractors.

    This service has no database, network, or filesystem-path capability. API code may
    call :meth:`validate_envelope`; worker code must call extraction through
    ``IsolatedDocumentParser`` rather than parsing inside a request or transaction.
    """

    def __init__(
        self,
        *,
        limits: DocumentLimits | None = None,
        registry: ExtractorRegistry | None = None,
    ) -> None:
        self._limits = limits or DocumentLimits()
        self._registry = registry or default_extractor_registry()

    @property
    def limits(self) -> DocumentLimits:
        return self._limits

    def validate_envelope(self, envelope: UploadEnvelope) -> ValidatedUpload:
        filename = _validate_filename(envelope.original_filename)
        if not isinstance(envelope.content, bytes):
            raise TypeError("document content must be immutable bytes")
        byte_size = len(envelope.content)
        if envelope.declared_size is not None:
            if envelope.declared_size < 0 or envelope.declared_size != byte_size:
                raise MalformedDocumentError("declared document size does not match received bytes")
        if byte_size == 0:
            raise MalformedDocumentError("document is empty")
        if byte_size > self._limits.max_input_bytes:
            raise DocumentTooLargeError("document exceeds the configured byte limit")

        suffix = PurePosixPath(filename).suffix.casefold()
        expected_media_type = _EXTENSION_MEDIA_TYPES.get(suffix)
        if expected_media_type is None:
            raise UnsupportedDocumentTypeError("document filename has an unsupported extension")
        declared_media_type = _normalize_declared_media_type(envelope.declared_mime_type)
        if declared_media_type is not expected_media_type:
            raise ContentTypeMismatchError(
                "declared content type does not agree with the filename extension"
            )

        detected_media_type = _detect_media_type(
            envelope.content,
            expected=expected_media_type,
        )
        if detected_media_type is not expected_media_type:
            raise ContentTypeMismatchError(
                "document bytes do not agree with the filename and declared content type"
            )
        return ValidatedUpload(
            original_filename=filename,
            validated_mime_type=detected_media_type,
            content_sha256=hashlib.sha256(envelope.content).hexdigest(),
            byte_size=byte_size,
            content=envelope.content,
        )

    def extract_validated(
        self,
        upload: ValidatedUpload,
        *,
        context: IngestionContext,
    ) -> ExtractionResult:
        """Extract bytes and attach trusted governance/provenance metadata."""

        _validate_context(context)
        extracted = self.extract_content(upload)
        return ExtractionResult(
            text=extracted.text,
            provenance=DocumentProvenance(
                sha256=upload.content_sha256,
                original_filename=upload.original_filename,
                validated_mime_type=upload.validated_mime_type.value,
                byte_size=upload.byte_size,
                extracted_character_count=len(extracted.text),
                page_count=extracted.page_count,
                section_count=extracted.section_count,
                parser_name=extracted.parser_name,
                parser_version=extracted.parser_version,
                ingestion_task_id=context.ingestion_task_id,
                owner_id=context.owner_id,
                sensitivity=context.sensitivity,
                retention_class=context.retention_class,
                source_timestamp=context.source_timestamp,
            ),
        )

    def extract_content(self, upload: ValidatedUpload) -> ExtractedContent:
        """Return parser output without accepting governance data in the parser boundary."""

        revalidated = self.validate_envelope(
            UploadEnvelope(
                original_filename=upload.original_filename,
                declared_mime_type=upload.validated_mime_type.value,
                declared_size=upload.byte_size,
                content=upload.content,
            )
        )
        if (
            revalidated.content_sha256 != upload.content_sha256
            or revalidated.validated_mime_type is not upload.validated_mime_type
        ):
            raise UnsafeDocumentError("staged document integrity check failed")

        extractor = self._registry.get(upload.validated_mime_type)
        extracted = extractor.extract(upload.content, limits=self._limits)
        if extracted.page_count is not None and extracted.page_count > self._limits.max_pages:
            raise DocumentStructureLimitError("document exceeds the configured page limit")
        if (
            extracted.section_count is not None
            and extracted.section_count > self._limits.max_sections
        ):
            raise DocumentStructureLimitError("document exceeds the configured section limit")
        return extracted


def _validate_filename(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("original filename must be a string")
    value = unicodedata.normalize("NFC", value)
    if (
        not value
        or value in {".", ".."}
        or value != value.strip()
        or "/" in value
        or "\\" in value
        or any(character in _CONFUSABLE_PATH_SEPARATORS for character in value)
        or any(unicodedata.category(character) == "Cf" for character in value)
        or _FORBIDDEN_FILENAME_CONTROLS.search(value)
        or len(value.encode("utf-8")) > 255
    ):
        raise InvalidFilenameError("original filename is not a safe basename")
    return value


def _normalize_declared_media_type(value: str) -> DocumentMediaType:
    if not isinstance(value, str):
        raise TypeError("declared MIME type must be a string")
    normalized = value.partition(";")[0].strip().casefold()
    try:
        return _MIME_ALIASES[normalized]
    except KeyError as exc:
        raise UnsupportedDocumentTypeError("declared content type is not supported") from exc


def _detect_media_type(
    content: bytes,
    *,
    expected: DocumentMediaType,
) -> DocumentMediaType:
    if content.startswith(b"%PDF-"):
        return DocumentMediaType.PDF
    if content.startswith(_ZIP_SIGNATURES):
        # The request process performs only bounded signature/MIME/extension checks.
        # ZIP central-directory parsing belongs exclusively to the approved parser
        # sandbox, where the DOCX extractor validates members and required parts.
        if expected is DocumentMediaType.DOCX:
            return DocumentMediaType.DOCX
        raise UnsupportedDocumentTypeError("ZIP archives other than DOCX are not supported")

    try:
        decoded = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        if expected in {DocumentMediaType.PDF, DocumentMediaType.DOCX}:
            raise ContentTypeMismatchError(
                "document does not contain the expected binary signature"
            ) from exc
        raise MalformedDocumentError("text documents must use valid UTF-8") from exc
    if "\x00" in decoded:
        raise MalformedDocumentError("document contains binary null bytes")
    if _HTML_SIGNATURE.match(decoded):
        return DocumentMediaType.HTML
    if expected is DocumentMediaType.HTML:
        raise ContentTypeMismatchError("HTML document bytes have no recognizable HTML structure")
    return (
        expected
        if expected in {DocumentMediaType.TEXT, DocumentMediaType.MARKDOWN}
        else DocumentMediaType.TEXT
    )


def _validate_context(context: IngestionContext) -> None:
    if not context.owner_id or len(context.owner_id) > 255:
        raise ValueError("owner_id must be non-empty and at most 255 characters")
    if context.sensitivity not in {"private", "shared", "restricted"}:
        raise ValueError("unsupported sensitivity")
    if context.retention_class not in {"keep", "ttl_30d", "ttl_90d", "legal_hold"}:
        raise ValueError("unsupported retention class")
    if context.source_timestamp.tzinfo is None or context.source_timestamp.utcoffset() is None:
        raise ValueError("source_timestamp must be timezone-aware")


__all__ = ["DocumentIngestionService"]
