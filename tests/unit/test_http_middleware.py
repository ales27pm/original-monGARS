from __future__ import annotations

import json
import tracemalloc
from collections.abc import Iterable
from typing import Any

import httpx
import pytest
from starlette.types import Message, Scope

from mongars.config import Environment, Settings
from mongars.http import RequestBodyLimitMiddleware
from mongars.main import create_app


def _scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
        "method": "POST",
        "root_path": "",
        "path": "/v1/tasks",
        "raw_path": b"/v1/tasks",
        "query_string": b"",
        "headers": headers or [],
        "state": {},
    }


def _receiver(messages: Iterable[Message]) -> Any:
    iterator = iter(messages)

    async def receive() -> Message:
        return next(iterator)

    return receive


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_length", [None, b"1"])
async def test_streamed_body_is_rejected_before_app(
    declared_length: bytes | None,
) -> None:
    downstream_called = False

    async def downstream(_scope: Scope, _receive: Any, _send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=8)
    headers = [] if declared_length is None else [(b"content-length", declared_length)]
    await middleware(
        _scope(headers),
        _receiver(
            [
                {"type": "http.request", "body": b"12345", "more_body": True},
                {"type": "http.request", "body": b"6789", "more_body": False},
            ]
        ),
        send,
    )

    assert downstream_called is False
    assert sent[0] == {
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-length", b"50"), (b"content-type", b"application/json")],
    }
    assert json.loads(sent[1]["body"]) == {"detail": "request body exceeds configured limit"}


@pytest.mark.asyncio
async def test_duplicate_content_length_is_rejected_before_body_read() -> None:
    downstream_called = False
    receive_called = False

    async def downstream(_scope: Scope, _receive: Any, _send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> Message:
        nonlocal receive_called
        receive_called = True
        raise AssertionError("invalid headers must be rejected before reading the body")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=8)
    await middleware(
        _scope([(b"content-length", b"4"), (b"content-length", b"4")]),
        receive,
        send,
    )

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": "invalid Content-Length header"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected_status", "expected_detail"),
    [
        (b"9", 413, "request body exceeds configured limit"),
        (b"not-a-number", 400, "invalid Content-Length header"),
        (b"-1", 400, "invalid Content-Length header"),
        (b"9" * 5_000, 400, "invalid Content-Length header"),
    ],
)
async def test_content_length_is_a_fast_rejection_path(
    header: bytes,
    expected_status: int,
    expected_detail: str,
) -> None:
    downstream_called = False
    receive_called = False

    async def downstream(_scope: Scope, _receive: Any, _send: Any) -> None:
        nonlocal downstream_called
        downstream_called = True

    async def receive() -> Message:
        nonlocal receive_called
        receive_called = True
        raise AssertionError("fast rejection must not read the request stream")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=8)
    await middleware(_scope([(b"content-length", header)]), receive, send)

    assert downstream_called is False
    assert receive_called is False
    assert sent[0]["status"] == expected_status
    assert json.loads(sent[1]["body"]) == {"detail": expected_detail}


@pytest.mark.asyncio
async def test_accepted_stream_is_replayed_unchanged() -> None:
    observed: Message | None = None

    async def downstream(_scope: Scope, receive: Any, send: Any) -> None:
        nonlocal observed
        observed = await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages: list[Message] = [
        {"type": "http.request", "body": b"1234", "more_body": True},
        {"type": "http.request", "body": b"5678", "more_body": False},
    ]
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=8)
    await middleware(
        _scope([(b"content-length", b"8")]),
        _receiver(messages),
        send,
    )

    assert observed == {
        "type": "http.request",
        "body": b"12345678",
        "more_body": False,
    }
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_heavily_fragmented_body_has_bounded_replay_memory() -> None:
    chunk_count = 100_000
    remaining = chunk_count
    observed: Message | None = None

    async def receive() -> Message:
        nonlocal remaining
        remaining -= 1
        return {
            "type": "http.request",
            "body": b"x",
            "more_body": remaining > 0,
        }

    async def downstream(_scope: Scope, replay: Any, send: Any) -> None:
        nonlocal observed
        observed = await replay()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(_message: Message) -> None:
        return None

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=chunk_count)
    tracemalloc.start()
    try:
        await middleware(_scope(), receive, send)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert observed == {
        "type": "http.request",
        "body": b"x" * chunk_count,
        "more_body": False,
    }
    assert peak < 2_000_000


@pytest.mark.asyncio
async def test_configured_cors_wraps_body_limit_errors() -> None:
    origin = "https://iphone.example.test"
    settings = Settings(
        environment=Environment.TEST,
        cors_origins=[origin],
        max_request_bytes=1_024,
    )
    application = create_app(settings=settings)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/v1/tasks",
                headers={"Origin": origin},
                content=b"x" * 1_025,
            )
    finally:
        await application.state.inference.aclose()
        await application.state.database.close()

    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == origin
