"""Secure Main document-ingestion capability."""

from mongars.ingestion.errors import (
    ContentTypeMismatchError,
    DocumentStructureLimitError,
    DocumentTooLargeError,
    EncryptedDocumentError,
    ExtractedTextTooLargeError,
    IngestionError,
    InvalidFilenameError,
    MalformedDocumentError,
    NoUsableTextError,
    ParserIsolationError,
    ParserResourceLimitError,
    ParserTimeoutError,
    UnsafeDocumentError,
    UnsupportedDocumentTypeError,
)
from mongars.ingestion.isolation import (
    DocumentParser,
    IsolatedDocumentParser,
    ParserProcessLimits,
)
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentMediaType,
    DocumentProvenance,
    ExtractionResult,
    IngestionContext,
    UploadEnvelope,
    ValidatedUpload,
)
from mongars.ingestion.service import DocumentIngestionService

__all__ = [
    "ContentTypeMismatchError",
    "DocumentIngestionService",
    "DocumentLimits",
    "DocumentMediaType",
    "DocumentParser",
    "DocumentProvenance",
    "DocumentStructureLimitError",
    "DocumentTooLargeError",
    "EncryptedDocumentError",
    "ExtractedTextTooLargeError",
    "ExtractionResult",
    "IngestionContext",
    "IngestionError",
    "InvalidFilenameError",
    "IsolatedDocumentParser",
    "MalformedDocumentError",
    "NoUsableTextError",
    "ParserIsolationError",
    "ParserProcessLimits",
    "ParserResourceLimitError",
    "ParserTimeoutError",
    "UnsafeDocumentError",
    "UnsupportedDocumentTypeError",
    "UploadEnvelope",
    "ValidatedUpload",
]
