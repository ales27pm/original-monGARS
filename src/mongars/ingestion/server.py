"""Secretless internal HTTP boundary for the document parser sandbox."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from mongars.http import RequestBodyLimitMiddleware
from mongars.ingestion.errors import IngestionError, ParserIsolationError
from mongars.ingestion.isolation import IsolatedDocumentParser, ParserProcessLimits
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ValidatedUpload

_UPLOAD_CHUNK_BYTES = 64 * 1024


class ParserServerSettings(BaseSettings):
    """Non-secret settings deliberately separate from the control-plane Settings."""

    model_config = SettingsConfigDict(
        env_prefix="MONGARS_PARSER_",
        case_sensitive=False,
        extra="ignore",
    )

    max_input_bytes: int = Field(default=10_000_000, ge=1_024, le=20_000_000)
    max_extracted_chars: int = Field(default=2_000_000, ge=1_000, le=20_000_000)
    max_pages: int = Field(default=500, ge=1, le=10_000)
    max_sections: int = Field(default=10_000, ge=1, le=100_000)
    max_archive_members: int = Field(default=2_000, ge=1, le=20_000)
    max_archive_uncompressed_bytes: int = Field(
        default=50_000_000,
        ge=1_024,
        le=250_000_000,
    )
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    process_memory_bytes: int = Field(
        default=536_870_912,
        ge=134_217_728,
        le=2_147_483_648,
    )

    def document_limits(self) -> DocumentLimits:
        return DocumentLimits(
            max_input_bytes=self.max_input_bytes,
            max_extracted_chars=self.max_extracted_chars,
            max_pages=self.max_pages,
            max_sections=self.max_sections,
            max_archive_members=self.max_archive_members,
            max_archive_uncompressed_bytes=self.max_archive_uncompressed_bytes,
            max_archive_member_bytes=min(
                self.max_archive_uncompressed_bytes,
                20_000_000,
            ),
        )


def create_parser_app(settings: ParserServerSettings | None = None) -> FastAPI:
    runtime_settings = settings or ParserServerSettings()
    limits = runtime_settings.document_limits()
    parser = IsolatedDocumentParser(
        document_limits=limits,
        process_limits=ParserProcessLimits(
            timeout_seconds=runtime_settings.timeout_seconds,
            cpu_seconds=max(1, min(int(runtime_settings.timeout_seconds), 60)),
            memory_bytes=runtime_settings.process_memory_bytes,
        ),
    )
    application = FastAPI(
        title="monGARS document parser",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=runtime_settings.max_input_bytes + 100_000,
    )

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.post("/extract")
    async def extract(
        file: Annotated[UploadFile, File()],
        content_sha256: Annotated[str, Form(pattern=r"^[0-9a-f]{64}$")],
        byte_size: Annotated[int, Form(ge=1, le=20_000_000)],
        validated_mime_type: Annotated[DocumentMediaType, Form()],
    ) -> JSONResponse:
        try:
            content = await _read_bounded_upload(file, max_bytes=limits.max_input_bytes)
            if byte_size != len(content):
                raise ParserIsolationError("parser request byte size does not match its content")
            if file.content_type != validated_mime_type.value:
                raise ParserIsolationError("parser request MIME type does not match its metadata")
            if hashlib.sha256(content).hexdigest() != content_sha256:
                raise ParserIsolationError("parser request digest does not match its content")
            result = await parser.extract(
                ValidatedUpload(
                    original_filename=file.filename or "",
                    validated_mime_type=validated_mime_type,
                    content_sha256=content_sha256,
                    byte_size=byte_size,
                    content=content,
                )
            )
        except IngestionError as exc:
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={"status": "error", "code": exc.code, "message": str(exc)[:500]},
            )
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "status": "error",
                    "code": ParserIsolationError.code,
                    "message": "document parser service failed unexpectedly",
                },
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ok",
                "text": result.text,
                "page_count": result.page_count,
                "section_count": result.section_count,
                "parser_name": result.parser_name,
                "parser_version": result.parser_version,
            },
        )

    return application


async def _read_bounded_upload(file: UploadFile, *, max_bytes: int) -> bytes:
    content = bytearray()
    try:
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            if len(content) + len(chunk) > max_bytes:
                raise ParserIsolationError("parser request exceeds its configured byte limit")
            content.extend(chunk)
    finally:
        await file.close()
    return bytes(content)


app = create_parser_app()


__all__ = ["ParserServerSettings", "app", "create_parser_app"]
