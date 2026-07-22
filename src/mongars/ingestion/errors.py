"""Stable, non-sensitive errors emitted by the document ingestion boundary."""

from __future__ import annotations

from typing import ClassVar


class IngestionError(ValueError):
    """Base class for errors safe to record in task and audit events."""

    code: ClassVar[str] = "ingestion_error"
    retryable: ClassVar[bool] = False


class InvalidFilenameError(IngestionError):
    code = "invalid_filename"


class UnsupportedDocumentTypeError(IngestionError):
    code = "unsupported_document_type"


class ContentTypeMismatchError(IngestionError):
    code = "content_type_mismatch"


class DocumentTooLargeError(IngestionError):
    code = "document_too_large"


class ExtractedTextTooLargeError(IngestionError):
    code = "extracted_text_too_large"


class DocumentStructureLimitError(IngestionError):
    code = "document_structure_limit"


class MalformedDocumentError(IngestionError):
    code = "malformed_document"


class EncryptedDocumentError(IngestionError):
    code = "encrypted_document"


class UnsafeDocumentError(IngestionError):
    code = "unsafe_document"


class NoUsableTextError(IngestionError):
    code = "no_usable_text"


class ParserIsolationError(IngestionError):
    code = "parser_isolation_error"
    retryable = True


class ParserTimeoutError(ParserIsolationError):
    code = "parser_timeout"


class ParserResourceLimitError(ParserIsolationError):
    code = "parser_resource_limit"


_ERROR_TYPES: dict[str, type[IngestionError]] = {
    error_type.code: error_type
    for error_type in (
        IngestionError,
        InvalidFilenameError,
        UnsupportedDocumentTypeError,
        ContentTypeMismatchError,
        DocumentTooLargeError,
        ExtractedTextTooLargeError,
        DocumentStructureLimitError,
        MalformedDocumentError,
        EncryptedDocumentError,
        UnsafeDocumentError,
        NoUsableTextError,
        ParserIsolationError,
        ParserTimeoutError,
        ParserResourceLimitError,
    )
}


def error_from_code(code: str, message: str) -> IngestionError:
    """Recreate a stable error returned by the parser subprocess."""

    return _ERROR_TYPES.get(code, ParserIsolationError)(message)


__all__ = [
    "ContentTypeMismatchError",
    "DocumentStructureLimitError",
    "DocumentTooLargeError",
    "EncryptedDocumentError",
    "ExtractedTextTooLargeError",
    "IngestionError",
    "InvalidFilenameError",
    "MalformedDocumentError",
    "NoUsableTextError",
    "ParserIsolationError",
    "ParserResourceLimitError",
    "ParserTimeoutError",
    "UnsafeDocumentError",
    "UnsupportedDocumentTypeError",
    "error_from_code",
]
