"""Secure Main document-ingestion capability."""

from mongars.ingestion.chunking import chunk_segments
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
    DocumentLocator,
    DocumentMediaType,
    DocumentProvenance,
    ExtractedSegment,
    ExtractionResult,
    IngestionContext,
    LocatedTextChunk,
    UploadEnvelope,
    ValidatedUpload,
)
from mongars.ingestion.service import DocumentIngestionService

__all__ = [
    "ContentTypeMismatchError",
    "DocumentIngestionService",
    "DocumentLimits",
    "DocumentLocator",
    "DocumentMediaType",
    "DocumentParser",
    "DocumentProvenance",
    "DocumentStructureLimitError",
    "DocumentTooLargeError",
    "EncryptedDocumentError",
    "ExtractedSegment",
    "ExtractedTextTooLargeError",
    "ExtractionResult",
    "IngestionContext",
    "IngestionError",
    "InvalidFilenameError",
    "IsolatedDocumentParser",
    "LocatedTextChunk",
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
    "chunk_segments",
]
