from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from mongars.api.dependencies import (
    DocumentUploadAdmissionDependency,
    DocumentUploadAdmissionRoute,
    PolicyDependency,
    PrincipalDependency,
    SessionDependency,
    SettingsDependency,
)
from mongars.api.schemas import DocumentUploadResponse
from mongars.events.repository import EventRepository
from mongars.ids import uuid7
from mongars.ingestion.errors import DocumentTooLargeError, IngestionError
from mongars.ingestion.models import UploadEnvelope
from mongars.ingestion.runtime import ingestion_service_from_settings
from mongars.ingestion.staging import DocumentStagingQuotaError, DocumentStagingRepository
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskService

router = APIRouter(
    prefix="/v1/documents",
    tags=["documents"],
    route_class=DocumentUploadAdmissionRoute,
)

_UPLOAD_CHUNK_BYTES = 64 * 1024


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    principal: PrincipalDependency,
    admission: DocumentUploadAdmissionDependency,
    session: SessionDependency,
    settings: SettingsDependency,
    policy: PolicyDependency,
    file: Annotated[UploadFile, File(description="TXT, Markdown, HTML, PDF, or DOCX")],
    declared_size: Annotated[int, Form(ge=1, le=20_000_000)],
    source_timestamp: Annotated[datetime, Form()],
    title: Annotated[str | None, Form(max_length=500)] = None,
    sensitivity: Annotated[
        Literal["private", "shared", "restricted"],
        Form(),
    ] = "private",
    retention_class: Annotated[
        Literal["keep", "ttl_30d", "ttl_90d", "legal_hold"],
        Form(),
    ] = "keep",
) -> DocumentUploadResponse:
    received_at = admission.received_at
    if declared_size > settings.max_document_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="document exceeds the configured byte limit",
        )
    if source_timestamp.tzinfo is None or source_timestamp.utcoffset() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="source_timestamp must include a timezone",
        )

    content = await _read_bounded_upload(file, max_bytes=settings.max_document_upload_bytes)
    try:
        validated = ingestion_service_from_settings(settings).validate_envelope(
            UploadEnvelope(
                original_filename=file.filename or "",
                declared_mime_type=file.content_type or "",
                content=content,
                declared_size=declared_size,
            )
        )
    except DocumentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    except IngestionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc

    staging_id = uuid7()
    normalized_title = title.strip() if title and title.strip() else None
    service = TaskService(
        settings=settings,
        repository=TaskRepository(session),
        events=EventRepository(session),
        policy=policy,
    )
    task = await service.create(
        owner_id=principal.subject,
        kind="document.ingest",
        payload={
            "staging_id": str(staging_id),
            "original_filename": validated.original_filename,
            "source_sha256": validated.content_sha256,
            "detected_mime_type": validated.validated_mime_type.value,
            "byte_size": validated.byte_size,
            "source_timestamp": source_timestamp.astimezone(UTC).isoformat(),
            "received_at": received_at.isoformat(),
            "source_time_basis": "user_supplied",
            "title": normalized_title,
            "sensitivity": sensitivity,
            "retention_class": retention_class,
        },
    )
    try:
        await DocumentStagingRepository(session).create(
            staging_id=staging_id,
            task_id=task.id,
            owner_id=principal.subject,
            original_filename=validated.original_filename,
            detected_mime_type=validated.validated_mime_type.value,
            source_sha256=bytes.fromhex(validated.content_sha256),
            content=validated.content,
            source_timestamp=source_timestamp.astimezone(UTC),
            received_at=received_at,
            ttl_seconds=settings.document_staging_ttl_seconds,
            max_owner_objects=settings.max_document_staged_objects,
            max_owner_bytes=settings.max_document_staged_bytes,
        )
    except DocumentStagingQuotaError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
        ) from exc
    return DocumentUploadResponse.from_model(task)


async def _read_bounded_upload(file: UploadFile, *, max_bytes: int) -> bytes:
    content = bytearray()
    try:
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            if len(content) + len(chunk) > max_bytes:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="document exceeds the configured byte limit",
                )
            content.extend(chunk)
    finally:
        await file.close()
    return bytes(content)


__all__ = ["router"]
