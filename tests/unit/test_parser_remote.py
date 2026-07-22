from __future__ import annotations

import json

import httpx
import pytest

from mongars.config import Environment, Settings
from mongars.ingestion.errors import ParserIsolationError, ParserTimeoutError
from mongars.ingestion.isolation import (
    IsolatedDocumentParser,
    ParserHealth,
    ParserProcessLimits,
)
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentMediaType,
    ExtractedContent,
    ValidatedUpload,
)
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
        "segments": [
            {
                "text": "isolated",
                "locator": {
                    "media_type": "text/plain",
                    "page_number": None,
                    "heading_path": [],
                    "block_index": 0,
                    "line_start": 1,
                    "line_end": 1,
                    "table_index": None,
                    "cell_reference": None,
                },
            }
        ],
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
    assert result.segments[0].locator.line_start == 1


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
async def test_remote_parser_rejects_forged_or_cross_media_locator() -> None:
    response = _ok_response()
    segments = response["segments"]
    assert isinstance(segments, list)
    segment = segments[0]
    assert isinstance(segment, dict)
    locator = segment["locator"]
    assert isinstance(locator, dict)
    locator["media_type"] = "application/pdf"
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
        with pytest.raises(ParserIsolationError, match="mismatched segment media type"):
            await parser.extract(_upload())
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["impossible_page", "inconsistent_text"])
async def test_remote_parser_rejects_impossible_or_inconsistent_provenance(
    mutation: str,
) -> None:
    response = _ok_response()
    segments = response["segments"]
    assert isinstance(segments, list)
    segment = segments[0]
    assert isinstance(segment, dict)
    if mutation == "impossible_page":
        locator = segment["locator"]
        assert isinstance(locator, dict)
        locator["page_number"] = 999
    else:
        segment["text"] = "different content"
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
        with pytest.raises(ParserIsolationError, match=r"impossible|inconsistent"):
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
async def test_remote_parser_health_is_bounded_and_reports_version() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("http://parser:8091/readyz")
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "parser_version": "mongars-parser-v1",
                "error_code": None,
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    parser = RemoteDocumentParser(
        base_url="http://parser:8091",
        document_limits=DocumentLimits(),
        timeout_seconds=2,
        client=client,
    )
    try:
        health = await parser.health()
    finally:
        await client.aclose()

    assert health.healthy is True
    assert health.parser_version == "mongars-parser-v1"
    assert health.error_code is None


@pytest.mark.asyncio
async def test_remote_parser_readiness_preserves_bounded_self_test_failure() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                503,
                json={
                    "status": "not_ready",
                    "parser_version": None,
                    "error_code": "parser_self_test_failed",
                },
                request=request,
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
        health = await parser.health()
    finally:
        await client.aclose()

    assert health == ParserHealth(
        healthy=False,
        parser_version=None,
        error_code="parser_self_test_failed",
    )


@pytest.mark.asyncio
async def test_remote_parser_health_rejects_oversized_chunked_body() -> None:
    class OversizedHealthStream(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            yield b"{" + b'"padding":"' + (b"x" * 40_000)
            yield b"y" * 40_000

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                stream=OversizedHealthStream(),
                request=request,
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
        health = await parser.health()
    finally:
        await client.aclose()

    assert health.healthy is False
    assert health.parser_version is None
    assert health.error_code == "unavailable"


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
        health = await parser.health()
        result = await parser.extract(_upload())
    finally:
        await client.aclose()

    assert health == ParserHealth(
        healthy=True,
        parser_version="mongars-parser-v1",
        error_code=None,
    )
    assert result.text == "isolated"
    assert result.section_count == 1


class StubDocumentParser:
    def __init__(self, health: ParserHealth) -> None:
        self._health = health
        self.health_calls = 0

    async def extract(self, upload: ValidatedUpload) -> ExtractedContent:
        raise AssertionError(f"unexpected extraction for {upload.original_filename}")

    async def health(self) -> ParserHealth:
        self.health_calls += 1
        return self._health

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_parser_liveness_is_cheap_and_readiness_runs_the_parser_probe() -> None:
    parser = StubDocumentParser(
        ParserHealth(
            healthy=False,
            parser_version=None,
            error_code="parser_self_test_failed",
        )
    )
    application = create_parser_app(document_parser=parser)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://parser",
    ) as client:
        liveness = await client.get("/healthz")
        assert liveness.status_code == 200
        assert parser.health_calls == 0

        readiness = await client.get("/readyz")

    assert readiness.status_code == 503
    assert readiness.json() == {
        "status": "not_ready",
        "parser_version": None,
        "error_code": "parser_self_test_failed",
    }
    assert parser.health_calls == 1


@pytest.mark.asyncio
async def test_local_parser_readiness_fails_closed_when_child_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = IsolatedDocumentParser(
        process_limits=ParserProcessLimits(timeout_seconds=1),
    )

    def fail_extraction(_upload: ValidatedUpload) -> ExtractedContent:
        raise ParserIsolationError("simulated child failure")

    monkeypatch.setattr(parser, "_extract_blocking", fail_extraction)

    assert await parser.health() == ParserHealth(
        healthy=False,
        parser_version=None,
        error_code="parser_self_test_failed",
    )
