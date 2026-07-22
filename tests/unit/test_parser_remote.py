from __future__ import annotations

import json

import httpx
import pytest

from mongars.config import Environment, Settings
from mongars.ingestion.errors import ParserIsolationError, ParserTimeoutError
from mongars.ingestion.models import DocumentLimits, DocumentMediaType, ValidatedUpload
from mongars.ingestion.remote import RemoteDocumentParser
from mongars.ingestion.runtime import document_parser_from_settings
from mongars.ingestion.server import ParserServerSettings, create_parser_app


def _upload() -> ValidatedUpload:
    return ValidatedUpload(
        original_filename="approved.txt",
        validated_mime_type=DocumentMediaType.TEXT,
        content_sha256="afed2f77cefe58d821f08e334aeb42e52facc63c4f97e9d5d18f53fd0e285953",
        byte_size=8,
        content=b"isolated",
    )


def _ok_response() -> dict[str, object]:
    return {
        "status": "ok",
        "text": "isolated",
        "page_count": None,
        "section_count": 1,
        "parser_name": "utf8-text",
        "parser_version": "1",
    }


def test_production_worker_requires_the_secretless_parser_sidecar() -> None:
    settings = Settings(
        environment=Environment.PRODUCTION,
        api_token="test-production-token",  # noqa: S106 - test-only value
        approval_hmac_key="test-production-hmac",
    )

    with pytest.raises(RuntimeError, match="DOCUMENT_PARSER_BASE_URL"):
        document_parser_from_settings(settings)


@pytest.mark.asyncio
async def test_remote_parser_sends_only_technical_metadata_and_validates_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        assert request.url == httpx.URL("http://parser:8091/extract")
        assert b"owner_id" not in body
        assert b"sensitivity" not in body
        assert b"retention_class" not in body
        assert b"ingestion_task_id" not in body
        assert b"approved.txt" in body
        assert b"text/plain" in body
        return httpx.Response(200, json=_ok_response())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    parser = RemoteDocumentParser(
        base_url="http://parser:8091",
        document_limits=DocumentLimits(),
        timeout_seconds=2,
        client=client,
    )
    try:
        result = await parser.extract(_upload())
    finally:
        await client.aclose()

    assert result.text == "isolated"
    assert result.parser_name == "utf8-text"


@pytest.mark.asyncio
async def test_remote_parser_rejects_forged_parser_identity() -> None:
    response = _ok_response()
    response["parser_name"] = "attacker-parser"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=response))
    )
    parser = RemoteDocumentParser(
        base_url="http://parser:8091",
        document_limits=DocumentLimits(),
        timeout_seconds=2,
        client=client,
    )
    try:
        with pytest.raises(ParserIsolationError, match="identity"):
            await parser.extract(_upload())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_remote_parser_rejects_oversized_response_before_json_decode() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-length": "99999999"},
                content=b"{}",
            )
        )
    )
    parser = RemoteDocumentParser(
        base_url="http://parser:8091",
        document_limits=DocumentLimits(max_extracted_chars=1_000),
        timeout_seconds=2,
        client=client,
    )
    try:
        with pytest.raises(ParserIsolationError, match="exceeded"):
            await parser.extract(_upload())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_remote_parser_recreates_stable_retryable_error() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                422,
                content=json.dumps(
                    {
                        "status": "error",
                        "code": "parser_timeout",
                        "message": "document parser timed out",
                    }
                ).encode(),
            )
        )
    )
    parser = RemoteDocumentParser(
        base_url="http://parser:8091",
        document_limits=DocumentLimits(),
        timeout_seconds=2,
        client=client,
    )
    try:
        with pytest.raises(ParserTimeoutError):
            await parser.extract(_upload())
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_secretless_parser_service_round_trip() -> None:
    application = create_parser_app(
        ParserServerSettings(
            max_input_bytes=10_000,
            max_extracted_chars=10_000,
            timeout_seconds=5,
        )
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://parser",
    )
    parser = RemoteDocumentParser(
        base_url="http://parser",
        document_limits=DocumentLimits(
            max_input_bytes=10_000,
            max_extracted_chars=10_000,
        ),
        timeout_seconds=5,
        client=client,
    )
    try:
        result = await parser.extract(_upload())
    finally:
        await client.aclose()

    assert result.text == "isolated"
    assert result.section_count == 1
