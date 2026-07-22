"""One-shot, resource-bounded subprocess execution for untrusted parsers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import signal
from collections.abc import Mapping
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Protocol, runtime_checkable

from mongars.ingestion.errors import (
    IngestionError,
    ParserIsolationError,
    ParserResourceLimitError,
    ParserTimeoutError,
    error_from_code,
)
from mongars.ingestion.extractors.text import normalize_text
from mongars.ingestion.models import (
    DocumentLimits,
    DocumentLocator,
    DocumentMediaType,
    ExtractedContent,
    ExtractedSegment,
    ValidatedUpload,
)
from mongars.ingestion.service import DocumentIngestionService

_RESULT_OVERHEAD_BYTES = 65_536
_MAX_LOCATOR_BYTES = 8_192
_MAX_ERROR_MESSAGE_CHARS = 500
_MAX_PARSER_ID_CHARS = 128
_READINESS_PROBE_CONTENT = b"monGARS parser readiness probe\n"
_READINESS_PROBE_TEXT = "monGARS parser readiness probe"
_EXPECTED_PARSER_NAMES: Mapping[DocumentMediaType, str] = {
    DocumentMediaType.TEXT: "utf8-text",
    DocumentMediaType.MARKDOWN: "markdown-plaintext",
    DocumentMediaType.HTML: "beautifulsoup-html",
    DocumentMediaType.PDF: "pypdf",
    DocumentMediaType.DOCX: "python-docx",
}


@runtime_checkable
class DocumentParser(Protocol):
    """Minimal boundary implemented by local and remote parser sandboxes."""

    async def extract(self, upload: ValidatedUpload) -> ExtractedContent: ...

    async def health(self) -> ParserHealth: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ParserHealth:
    healthy: bool
    parser_version: str | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ParserProcessLimits:
    timeout_seconds: float = 20.0
    cpu_seconds: int = 15
    memory_bytes: int = 768 * 1024 * 1024
    max_open_files: int = 64

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("parser timeout must be positive")
        if self.cpu_seconds <= 0 or self.memory_bytes <= 0 or self.max_open_files < 16:
            raise ValueError("parser process limits are invalid")


class IsolatedDocumentParser:
    """Parse one immutable staged object in a disposable child process.

    The child receives approved bytes and technical metadata only. Governance never
    crosses this boundary. Child results use bounded JSON bytes rather than pickle, so
    a compromised child cannot make the trusted parent deserialize executable objects.
    Production runs this process inside the separate, secretless parser sidecar.
    """

    def __init__(
        self,
        *,
        document_limits: DocumentLimits | None = None,
        process_limits: ParserProcessLimits | None = None,
    ) -> None:
        self._document_limits = document_limits or DocumentLimits()
        self._process_limits = process_limits or ParserProcessLimits()

    async def extract(self, upload: ValidatedUpload) -> ExtractedContent:
        """Run extraction without blocking the worker event loop."""

        return await asyncio.to_thread(self._extract_blocking, upload)

    async def health(self) -> ParserHealth:
        probe = ValidatedUpload(
            original_filename="readiness.txt",
            validated_mime_type=DocumentMediaType.TEXT,
            content_sha256=hashlib.sha256(_READINESS_PROBE_CONTENT).hexdigest(),
            byte_size=len(_READINESS_PROBE_CONTENT),
            content=_READINESS_PROBE_CONTENT,
        )
        try:
            result = await self.extract(probe)
        except Exception:
            return ParserHealth(
                healthy=False,
                parser_version=None,
                error_code="parser_self_test_failed",
            )
        if (
            result.text != _READINESS_PROBE_TEXT
            or result.parser_name != _EXPECTED_PARSER_NAMES[DocumentMediaType.TEXT]
            or result.section_count != 1
        ):
            return ParserHealth(
                healthy=False,
                parser_version=None,
                error_code="parser_self_test_failed",
            )
        return ParserHealth(healthy=True, parser_version="local-isolated-v1")

    async def aclose(self) -> None:
        """Local one-shot parser processes retain no shared resources."""

    def _extract_blocking(self, upload: ValidatedUpload) -> ExtractedContent:
        process_context = multiprocessing.get_context("spawn")
        receive_connection, send_connection = process_context.Pipe(duplex=False)
        process = process_context.Process(
            target=_parser_child,
            args=(
                send_connection,
                upload,
                self._document_limits,
                self._process_limits,
            ),
            name="mongars-document-parser",
            daemon=True,
        )
        max_result_bytes = (
            self._document_limits.max_extracted_chars * 8
            + self._document_limits.max_sections * _MAX_LOCATOR_BYTES
            + _RESULT_OVERHEAD_BYTES
        )
        try:
            process.start()
            send_connection.close()
            if not receive_connection.poll(self._process_limits.timeout_seconds):
                _terminate_process(process)
                raise ParserTimeoutError("document parser exceeded its wall-clock time limit")
            try:
                raw_message = receive_connection.recv_bytes(maxlength=max_result_bytes)
            except EOFError as exc:
                process.join(timeout=1)
                raise _process_exit_error(process.exitcode) from exc
            except OSError as exc:
                _terminate_process(process)
                raise ParserIsolationError(
                    "document parser returned an oversized or invalid result"
                ) from exc
            process.join(timeout=1)
            if process.is_alive():
                _terminate_process(process)
                raise ParserIsolationError("document parser did not exit after returning a result")
            return decode_parser_response(
                raw_message,
                upload=upload,
                limits=self._document_limits,
            )
        except OSError as exc:
            if process.is_alive():
                _terminate_process(process)
            raise ParserIsolationError("document parser process could not be started") from exc
        finally:
            receive_connection.close()
            send_connection.close()
            if process.is_alive():
                _terminate_process(process)


def _parser_child(
    connection: Connection,
    upload: ValidatedUpload,
    document_limits: DocumentLimits,
    process_limits: ParserProcessLimits,
) -> None:
    try:
        _scrub_child_environment()
        _apply_resource_limits(process_limits)
        result = DocumentIngestionService(limits=document_limits).extract_content(upload)
        message = {
            "status": "ok",
            "text": result.text,
            "segments": [
                {"text": segment.text, "locator": segment.locator.as_dict()}
                for segment in result.segments
            ],
            "page_count": result.page_count,
            "section_count": result.section_count,
            "parser_name": result.parser_name,
            "parser_version": result.parser_version,
        }
    except IngestionError as exc:
        message = {
            "status": "error",
            "code": exc.code,
            "message": str(exc)[:_MAX_ERROR_MESSAGE_CHARS],
        }
    except MemoryError:
        message = {
            "status": "error",
            "code": ParserResourceLimitError.code,
            "message": "document parser exceeded its memory limit",
        }
    except BaseException:
        # Never serialize exception representations or tracebacks from untrusted parsers.
        message = {
            "status": "error",
            "code": ParserIsolationError.code,
            "message": "document parser failed unexpectedly",
        }
    try:
        connection.send_bytes(
            json.dumps(
                message,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    finally:
        connection.close()


def decode_parser_response(
    raw_message: bytes,
    *,
    upload: ValidatedUpload,
    limits: DocumentLimits,
) -> ExtractedContent:
    try:
        message = json.loads(raw_message)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParserIsolationError("document parser returned invalid JSON") from exc
    if not isinstance(message, dict) or not isinstance(message.get("status"), str):
        raise ParserIsolationError("document parser returned an invalid result envelope")

    if message["status"] == "error":
        if set(message) != {"status", "code", "message"}:
            raise ParserIsolationError("document parser returned an invalid error envelope")
        code = message.get("code")
        detail = message.get("message")
        if (
            not isinstance(code, str)
            or not code
            or len(code) > 100
            or not isinstance(detail, str)
            or not detail
            or len(detail) > _MAX_ERROR_MESSAGE_CHARS
        ):
            raise ParserIsolationError("document parser returned an invalid error envelope")
        raise error_from_code(code, detail)

    expected_keys = {
        "status",
        "text",
        "segments",
        "page_count",
        "section_count",
        "parser_name",
        "parser_version",
    }
    if message["status"] != "ok" or set(message) != expected_keys:
        raise ParserIsolationError("document parser returned an invalid result envelope")
    text = message.get("text")
    parser_name = message.get("parser_name")
    parser_version = message.get("parser_version")
    if (
        not isinstance(text, str)
        or normalize_text(text, max_chars=limits.max_extracted_chars) != text
    ):
        raise ParserIsolationError("document parser returned invalid normalized text")
    if parser_name != _EXPECTED_PARSER_NAMES[upload.validated_mime_type]:
        raise ParserIsolationError("document parser returned an unexpected parser identity")
    if (
        not isinstance(parser_version, str)
        or not parser_version
        or parser_version != parser_version.strip()
        or len(parser_version) > _MAX_PARSER_ID_CHARS
    ):
        raise ParserIsolationError("document parser returned an invalid parser version")
    page_count = _optional_bounded_count(
        message.get("page_count"),
        field="page count",
        maximum=limits.max_pages,
    )
    section_count = _optional_bounded_count(
        message.get("section_count"),
        field="section count",
        maximum=limits.max_sections,
    )
    raw_segments = message.get("segments")
    if (
        not isinstance(raw_segments, list)
        or not raw_segments
        or len(raw_segments) > limits.max_sections
    ):
        raise ParserIsolationError("document parser returned invalid structured segments")
    segments: list[ExtractedSegment] = []
    segment_characters = 0
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict) or set(raw_segment) != {"text", "locator"}:
            raise ParserIsolationError("document parser returned an invalid structured segment")
        segment_text = raw_segment.get("text")
        if not isinstance(segment_text, str):
            raise ParserIsolationError("document parser returned invalid structured segment text")
        try:
            normalized_segment = normalize_text(
                segment_text,
                max_chars=limits.max_extracted_chars,
            )
            locator = DocumentLocator.from_dict(raw_segment.get("locator"))
        except (IngestionError, TypeError, ValueError) as exc:
            raise ParserIsolationError(
                "document parser returned an invalid structured segment"
            ) from exc
        if normalized_segment != segment_text:
            raise ParserIsolationError("document parser returned unnormalized segment text")
        if locator.media_type != upload.validated_mime_type.value:
            raise ParserIsolationError("document parser returned a mismatched segment media type")
        try:
            locator.validate_for_document(
                media_type=upload.validated_mime_type,
                page_count=page_count,
                maximum_blocks=limits.max_sections,
            )
        except ValueError as exc:
            raise ParserIsolationError(
                "document parser returned impossible segment provenance"
            ) from exc
        if (
            locator.line_start is not None
            and locator.line_end is not None
            and len(segment_text.splitlines()) > locator.line_end - locator.line_start + 1
        ):
            raise ParserIsolationError("document parser returned an impossible source line range")
        segment_characters += len(segment_text)
        if segment_characters > limits.max_extracted_chars:
            raise ParserIsolationError("document parser returned oversized structured segments")
        segments.append(ExtractedSegment(text=segment_text, locator=locator))
    if "\n\n".join(segment.text for segment in segments) != text:
        raise ParserIsolationError("document parser returned inconsistent canonical text")
    return ExtractedContent(
        text=text,
        segments=tuple(segments),
        page_count=page_count,
        section_count=section_count,
        parser_name=parser_name,
        parser_version=parser_version,
    )


def _optional_bounded_count(value: object, *, field: str, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise ParserIsolationError(f"document parser returned an invalid {field}")
    return value


def _scrub_child_environment() -> None:
    temporary_directory = os.path.join(os.sep, "tmp")
    os.environ.clear()
    os.environ.update(
        {
            "HOME": "/nonexistent",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": temporary_directory,
            "TZ": "UTC",
        }
    )
    os.chdir(temporary_directory)
    os.umask(0o077)


def _apply_resource_limits(limits: ParserProcessLimits) -> None:
    try:
        import resource
    except ImportError as exc:  # pragma: no cover - Ubuntu always provides resource
        raise ParserIsolationError("parser resource limits are unavailable") from exc

    cpu_hard_limit = limits.cpu_seconds + 1
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, cpu_hard_limit))
    resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (limits.max_open_files, limits.max_open_files))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def _terminate_process(process: BaseProcess) -> None:
    process.terminate()
    process.join(timeout=1)
    if process.is_alive():
        process.kill()
        process.join(timeout=1)


def _process_exit_error(exit_code: int | None) -> ParserIsolationError:
    resource_signals = {-signal.SIGXCPU, -signal.SIGKILL, -signal.SIGSEGV}
    if exit_code in resource_signals:
        return ParserResourceLimitError("document parser exceeded a process resource limit")
    return ParserIsolationError("document parser exited without returning a result")


__all__ = [
    "DocumentParser",
    "IsolatedDocumentParser",
    "ParserProcessLimits",
    "decode_parser_response",
]
