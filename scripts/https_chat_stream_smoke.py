#!/usr/bin/env python3
"""Verify a deployed monGARS NDJSON chat stream over certificate-validated HTTPS."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPSHandler,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

_MAX_LINE_BYTES = 1_000_000
_MAX_FRAMES = 10_000
_EVIDENCE_PREFIX = {
    "conversation": "H",
    "memory": "M",
    "policy": "P",
    "web": "W",
}


class StreamSmokeError(RuntimeError):
    """Raised when the deployed stream violates its public protocol."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    url = _validated_stream_url(arguments.url)
    token = _read_secret(arguments.token_file)
    context = ssl.create_default_context(cafile=str(arguments.ca_file))
    payload = json.dumps(
        {
            "message": arguments.message,
            "require_local_only": True,
            "web_search": arguments.web_search,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/x-ndjson",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "monGARS-deployment-stream-smoke/1",
        },
    )
    opener = build_opener(
        ProxyHandler({}),
        _NoRedirects(),
        HTTPSHandler(context=context),
    )

    try:
        with opener.open(request, timeout=arguments.timeout) as response:  # noqa: S310
            if response.status != 200:
                raise StreamSmokeError(f"stream returned HTTP {response.status}, expected 200")
            media_type = response.headers.get_content_type().casefold()
            if media_type != "application/x-ndjson":
                raise StreamSmokeError(f"unexpected stream content type: {media_type}")
            if response.headers.get("Cache-Control", "").casefold() != "no-store":
                raise StreamSmokeError("stream response is missing Cache-Control: no-store")
            frames = list(_frames(response))
    except HTTPError as exc:
        detail = exc.read(16_384).decode("utf-8", errors="replace")
        raise StreamSmokeError(f"stream returned HTTP {exc.code}: {detail[:200]}") from exc
    except (TimeoutError, URLError) as exc:
        raise StreamSmokeError(f"stream connection failed: {type(exc).__name__}") from exc

    summary = _validate(frames)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Full HTTPS /v1/chat/stream URL")
    parser.add_argument("--ca-file", required=True, type=Path)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument(
        "--message",
        default="Return the exact phrase STREAM_SMOKE_OK.",
    )
    parser.add_argument(
        "--web-search",
        choices=("off", "auto", "required"),
        default="off",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser


def _validated_stream_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1/chat/stream"
    ):
        raise StreamSmokeError(
            "stream URL must be an HTTPS /v1/chat/stream endpoint without credentials, query, or fragment"
        )
    return value.strip()


def _read_secret(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token or "\n" in token or "\r" in token:
        raise StreamSmokeError("token file must contain one non-empty line")
    return token


def _frames(response: Any) -> Iterable[dict[str, Any]]:
    count = 0
    while True:
        raw = response.readline(_MAX_LINE_BYTES + 2)
        if not raw:
            return
        count += 1
        if count > _MAX_FRAMES:
            raise StreamSmokeError("stream emitted too many frames")
        if len(raw) > _MAX_LINE_BYTES + 1:
            raise StreamSmokeError("stream frame exceeds the byte ceiling")
        if not raw.endswith(b"\n"):
            raise StreamSmokeError("stream ended with an unterminated frame")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StreamSmokeError("stream emitted invalid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise StreamSmokeError("stream emitted a non-object frame")
        yield value


def _validate(frames: list[dict[str, Any]]) -> dict[str, Any]:
    if len(frames) < 3:
        raise StreamSmokeError("stream did not emit start, sources, and a terminal frame")
    if frames[0].get("type") != "start":
        raise StreamSmokeError("first frame is not start")
    if frames[1].get("type") != "sources":
        raise StreamSmokeError("second frame is not sources")

    trace_id = _string(frames[0], "trace_id", maximum=128)
    session_id = _string(frames[0], "session_id", maximum=64)
    raw_sources = frames[1].get("sources")
    if not isinstance(raw_sources, list):
        raise StreamSmokeError("sources frame does not contain a list")
    source_keys = {_source_key(item) for item in raw_sources}

    deltas: list[str] = []
    terminal: dict[str, Any] | None = None
    for frame in frames[2:]:
        frame_type = frame.get("type")
        if terminal is not None:
            raise StreamSmokeError("stream emitted a frame after its terminal frame")
        if frame_type == "delta":
            text = _string(frame, "text", maximum=_MAX_LINE_BYTES)
            if "<think" in text.casefold() or "</think" in text.casefold():
                raise StreamSmokeError("stream exposed a hidden-reasoning marker")
            deltas.append(text)
            continue
        if frame_type in {"final", "error"}:
            terminal = frame
            continue
        raise StreamSmokeError(f"unexpected stream frame type: {frame_type!r}")

    if terminal is None:
        raise StreamSmokeError("stream ended without a terminal frame")
    if terminal.get("type") == "error":
        code = _string(terminal, "code", maximum=100)
        raise StreamSmokeError(f"stream returned application error: {code}")
    if terminal.get("trace_id") != trace_id or terminal.get("session_id") != session_id:
        raise StreamSmokeError("final frame changed the stream identity")

    answer = _string(terminal, "answer", maximum=_MAX_LINE_BYTES)
    if "".join(deltas) != answer:
        raise StreamSmokeError("delta text does not exactly match the final answer")
    raw_citations = terminal.get("citations")
    if not isinstance(raw_citations, list):
        raise StreamSmokeError("final frame does not contain a citation list")
    citation_keys = {_source_key(item) for item in raw_citations}
    if not citation_keys.issubset(source_keys):
        raise StreamSmokeError("final frame cites evidence absent from the source catalog")

    return {
        "answer_characters": len(answer),
        "citation_count": len(citation_keys),
        "delta_count": len(deltas),
        "frame_count": len(frames),
        "session_id": session_id,
        "status": "ok",
        "trace_id": trace_id,
    }


def _source_key(value: object) -> str:
    if not isinstance(value, Mapping):
        raise StreamSmokeError("source entry is not an object")
    key = _string(value, "key", maximum=16)
    kind = _string(value, "kind", maximum=20)
    prefix = _EVIDENCE_PREFIX.get(kind)
    if prefix is None or not key.startswith(prefix):
        raise StreamSmokeError("source key does not match its evidence kind")
    return key


def _string(value: Mapping[str, object], key: str, *, maximum: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > maximum:
        raise StreamSmokeError(f"invalid {key}")
    return item


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except StreamSmokeError as exc:
        print(f"stream smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
