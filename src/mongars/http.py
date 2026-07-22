from __future__ import annotations

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    """Reject HTTP request bodies that exceed a bounded in-memory envelope.

    ``Content-Length`` is an optimization, not a security boundary. The complete ASGI
    request stream is counted before the downstream application is invoked, so chunked
    requests and requests with an understated or missing length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_lengths = [
            value.strip() for name, value in scope["headers"] if name.lower() == b"content-length"
        ]
        if raw_lengths:
            if len(raw_lengths) != 1 or not raw_lengths[0].isdigit() or len(raw_lengths[0]) > 20:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid Content-Length header",
                )
                return
            try:
                declared_length = int(raw_lengths[0])
            except (ValueError, OverflowError):
                await self._send_error(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid Content-Length header",
                )
                return
            if declared_length > self.max_bytes:
                await self._send_too_large(scope, receive, send)
                return

        body = bytearray()
        disconnected = False
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                break
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_bytes:
                await self._send_too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False
        replay_body = bytes(body)

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                if disconnected:
                    return {"type": "http.disconnect"}
                return {
                    "type": "http.request",
                    "body": replay_body,
                    "more_body": False,
                }
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _send_too_large(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._send_error(
            scope,
            receive,
            send,
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="request body exceeds configured limit",
        )

    @staticmethod
    async def _send_error(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)
