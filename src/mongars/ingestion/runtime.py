from __future__ import annotations

from mongars.config import Environment, Settings
from mongars.ingestion.isolation import (
    DocumentParser,
    IsolatedDocumentParser,
    ParserProcessLimits,
)
from mongars.ingestion.models import DocumentLimits
from mongars.ingestion.remote import RemoteDocumentParser
from mongars.ingestion.service import DocumentIngestionService


def document_limits_from_settings(settings: Settings) -> DocumentLimits:
    return DocumentLimits(
        max_input_bytes=settings.max_document_upload_bytes,
        max_extracted_chars=settings.max_document_chars,
        max_pages=settings.max_document_pages,
        max_sections=settings.max_document_sections,
        max_archive_members=settings.max_document_archive_entries,
        max_archive_uncompressed_bytes=settings.max_document_archive_uncompressed_bytes,
        max_archive_member_bytes=min(
            settings.max_document_archive_uncompressed_bytes,
            20_000_000,
        ),
    )


def ingestion_service_from_settings(settings: Settings) -> DocumentIngestionService:
    return DocumentIngestionService(limits=document_limits_from_settings(settings))


def document_parser_from_settings(settings: Settings) -> DocumentParser:
    timeout = settings.document_parser_timeout_seconds
    limits = document_limits_from_settings(settings)
    if settings.document_parser_base_url is not None:
        return RemoteDocumentParser(
            base_url=settings.document_parser_base_url,
            document_limits=limits,
            timeout_seconds=timeout,
        )
    if settings.environment is Environment.PRODUCTION:
        raise RuntimeError("MONGARS_DOCUMENT_PARSER_BASE_URL is required for the production worker")
    return IsolatedDocumentParser(
        document_limits=limits,
        process_limits=ParserProcessLimits(
            timeout_seconds=timeout,
            cpu_seconds=max(1, min(int(timeout), 60)),
            memory_bytes=settings.document_parser_memory_bytes,
        ),
    )


__all__ = [
    "document_limits_from_settings",
    "document_parser_from_settings",
    "ingestion_service_from_settings",
]
