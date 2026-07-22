from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.api.dependencies import get_session
from mongars.api.routes import documents
from mongars.config import Environment, Settings
from mongars.ingestion import staging as staging_module
from mongars.ingestion.concurrency import DocumentUploadAdmissionController
from mongars.ingestion.staging import DocumentStagingRepository
from mongars.security.auth import BearerTokenAuth
from mongars.security.policy import ActionClassification, ToolPolicy


def _settings(*, global_limit: int = 2, per_owner_limit: int = 1) -> Settings:
    return Settings(
        environment=Environment.TEST,
        owner_id="upload-owner",
        api_token=SecretStr("upload-boundary-test-token"),
        approval_hmac_key=SecretStr("upload-boundary-test-approval-key"),
        max_concurrent_document_uploads=global_limit,
        max_concurrent_document_uploads_per_owner=per_owner_limit,
    )


def test_upload_admission_enforces_global_and_per_owner_limits() -> None:
    admission = DocumentUploadAdmissionController(global_limit=2, per_owner_limit=1)
    before = datetime.now(UTC)

    owner_a = admission.try_acquire(owner_id="owner-a")
    assert owner_a is not None
    assert before <= owner_a.received_at <= datetime.now(UTC)
    assert owner_a.received_at.utcoffset() == UTC.utcoffset(owner_a.received_at)
    assert admission.try_acquire(owner_id="owner-a") is None

    owner_b = admission.try_acquire(owner_id="owner-b")
    assert owner_b is not None
    assert admission.try_acquire(owner_id="owner-c") is None
    assert admission.snapshot(owner_id="owner-a").active_global == 2
    assert admission.snapshot(owner_id="owner-a").active_for_owner == 1

    owner_a.release()
    owner_a.release()
    assert admission.snapshot(owner_id="owner-a").active_global == 1
    assert admission.snapshot(owner_id="owner-a").active_for_owner == 0

    owner_c = admission.try_acquire(owner_id="owner-c")
    assert owner_c is not None
    owner_b.release()
    owner_c.release()
    assert admission.snapshot(owner_id="owner-c").active_global == 0


class AggregateResult:
    def one(self) -> tuple[int, int]:
        return (0, 0)


class CapturingStagingSession:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.staged: Any = None
        self.flushed = False

    async def execute(self, _statement: object, _parameters: object = None) -> object:
        self.execute_calls += 1
        if self.execute_calls == 2:
            return AggregateResult()
        return None

    def add(self, staged: object) -> None:
        self.staged = staged

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_staging_ttl_starts_at_fresh_persistence_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_at = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    persistence_time = received_at + timedelta(minutes=10)
    ttl_seconds = 900

    class PersistenceClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            assert tz is UTC
            return persistence_time

    monkeypatch.setattr(staging_module, "datetime", PersistenceClock)
    session = CapturingStagingSession()
    content = b"bounded staged document"
    staged = await DocumentStagingRepository(cast(AsyncSession, session)).create(
        staging_id=uuid4(),
        task_id=uuid4(),
        owner_id="upload-owner",
        original_filename="notes.txt",
        detected_mime_type="text/plain",
        source_sha256=hashlib.sha256(content).digest(),
        content=content,
        source_timestamp=received_at - timedelta(days=1),
        received_at=received_at,
        ttl_seconds=ttl_seconds,
        max_owner_objects=2,
        max_owner_bytes=10_000,
    )

    assert session.flushed is True
    assert session.staged is staged
    assert staged.received_at == received_at
    assert staged.expires_at == persistence_time + timedelta(seconds=ttl_seconds)


class ExplodingBodyStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.iterated = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        raise AssertionError("saturated upload request body was read")
        yield b""  # pragma: no cover

    async def aclose(self) -> None:
        return None


def _document_application(
    *,
    settings: Settings,
    admission: DocumentUploadAdmissionController,
) -> FastAPI:
    application = FastAPI()
    application.state.settings = settings
    application.state.auth = BearerTokenAuth(settings, subject=settings.owner_id)
    application.state.policy = ToolPolicy(
        {("document", "ingest"): ActionClassification.LOCAL_MUTATION}
    )
    application.state.document_upload_admission = admission

    async def unused_session() -> AsyncIterator[AsyncSession]:
        yield cast(AsyncSession, object())

    application.dependency_overrides[get_session] = unused_session
    application.include_router(documents.router)
    return application


@pytest.mark.asyncio
async def test_saturated_upload_returns_429_without_reading_request_body() -> None:
    settings = _settings(global_limit=1, per_owner_limit=1)
    admission = DocumentUploadAdmissionController(global_limit=1, per_owner_limit=1)
    held = admission.try_acquire(owner_id=settings.owner_id)
    assert held is not None
    stream = ExplodingBodyStream()
    application = _document_application(settings=settings, admission=admission)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/v1/documents",
                headers={
                    "Authorization": "Bearer upload-boundary-test-token",
                    "Content-Type": "multipart/form-data; boundary=never-consumed",
                },
                content=stream,
            )

        assert response.status_code == 429
        assert response.json() == {"detail": "document upload concurrency limit reached"}
        assert response.headers["Retry-After"] == "1"
        assert stream.iterated is False
        assert admission.snapshot(owner_id=settings.owner_id).active_global == 1
    finally:
        held.release()


@pytest.mark.asyncio
async def test_unauthenticated_upload_is_rejected_without_reading_request_body() -> None:
    settings = _settings(global_limit=1, per_owner_limit=1)
    admission = DocumentUploadAdmissionController(global_limit=1, per_owner_limit=1)
    stream = ExplodingBodyStream()
    application = _document_application(settings=settings, admission=admission)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/documents",
            headers={"Content-Type": "multipart/form-data; boundary=never-consumed"},
            content=stream,
        )

    assert response.status_code == 401
    assert stream.iterated is False
    assert admission.snapshot(owner_id=settings.owner_id).active_global == 0


@pytest.mark.asyncio
async def test_upload_permit_releases_when_endpoint_rejects_envelope() -> None:
    settings = _settings(global_limit=1, per_owner_limit=1)
    admission = DocumentUploadAdmissionController(global_limit=1, per_owner_limit=1)
    application = _document_application(settings=settings, admission=admission)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/documents",
            headers={"Authorization": "Bearer upload-boundary-test-token"},
            files={"file": ("notes.txt", b"small body", "text/plain")},
            data={
                "declared_size": "20000000",
                "source_timestamp": "2026-07-22T12:30:00Z",
            },
        )

    assert response.status_code == 413
    assert admission.snapshot(owner_id=settings.owner_id).active_global == 0
    assert admission.snapshot(owner_id=settings.owner_id).active_for_owner == 0


@pytest.mark.asyncio
async def test_upload_permit_releases_when_multipart_parsing_fails() -> None:
    settings = _settings(global_limit=1, per_owner_limit=1)
    admission = DocumentUploadAdmissionController(global_limit=1, per_owner_limit=1)
    application = _document_application(settings=settings, admission=admission)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/v1/documents",
            headers={
                "Authorization": "Bearer upload-boundary-test-token",
                "Content-Type": "multipart/form-data; boundary=missing-body-boundary",
            },
            content=b"not a valid multipart body",
        )

    assert response.status_code in {400, 422}
    assert admission.snapshot(owner_id=settings.owner_id).active_global == 0
    assert admission.snapshot(owner_id=settings.owner_id).active_for_owner == 0
