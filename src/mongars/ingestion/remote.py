"""Bounded client for the secretless document-parser sidecar."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from mongars.ingestion.errors import ParserIsolationError, ParserTimeoutError
from mongars.ingestion.isolation import decode_parser_response
from mongars.ingestion.models import DocumentLimits, ExtractedContent, ValidatedUpload

_RESULT_OVERHEAD_BYTES = 65_536
_CONTENT_LENGTH = re.compile(r"^[0-9]{1,20}$")


class RemoteDocumentParser:
    """Copy approved bytes to a parser service outside the worker trust zone."""

    def __init__(
        self,
        *,
        base_url: str,
        document_limits: DocumentLimits,
        timeout_seconds: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        self._document_limits = document_limits
        if timeout_seconds <= 0:
            raise ValueError("parser timeout must be positive")
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_response_bytes = document_limits.max_extracted_chars * 4 + _RESULT_OVERHEAD_BYTES
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    async def extract(self, upload: ValidatedUpload) -> ExtractedContent:
        form_data = {
            "content_sha256": upload.content_sha256,
            "byte_size": str(upload.byte_size),
            "validated_mime_type": upload.validated_mime_type.value,
        }
        files = {
            "file": (
                upload.original_filename,
                upload.content,
                upload.validated_mime_type.value,
            )
        }
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}/extract",
                data=form_data,
                files=files,
                timeout=self._timeout,
            ) as response:
                raw_content_length = response.headers.get("content-length")
                if raw_content_length is not None:
                    if not _CONTENT_LENGTH.fullmatch(raw_content_length.strip()):
                        raise ParserIsolationError(
                            "document parser returned an invalid Content-Length"
                        )
                    if int(raw_content_length) > self._max_response_bytes:
                        raise ParserIsolationError("document parser response exceeded its limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > self._max_response_bytes:
                        raise ParserIsolationError("document parser response exceeded its limit")
                    body.extend(chunk)
        except httpx.TimeoutException as exc:
            raise ParserTimeoutError("document parser service timed out") from exc
        except httpx.RequestError as exc:
            raise ParserIsolationError("document parser service is unavailable") from exc

        return decode_parser_response(
            bytes(body),
            upload=upload,
            limits=self._document_limits,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _validate_base_url(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("document parser base URL must be a non-empty trimmed string")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("document parser base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("document parser base URL must not include credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("document parser base URL must be an origin without path or query")
    return value.rstrip("/")


__all__ = ["RemoteDocumentParser"]
