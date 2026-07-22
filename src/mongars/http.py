from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from fastapi import status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Count the live ASGI stream without buffering or replaying request bodies.

    ``Content-Length`` is only a fast rejection path. A wrapped ``receive`` counts every
    chunk consumed by Starlette, preserving UploadFile spooling and preventing chunked,
    missing-length, or understated-length requests from bypassing the limit.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        path_limits: Mapping[str, int] | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes
        normalized_path_limits = dict(path_limits or {})
        if any(
            not path.startswith("/") or limit < 1 for path, limit in normalized_path_limits.items()
        ):
            raise ValueError("path limits require absolute paths and positive byte limits")
        self.path_limits = MappingProxyType(normalized_path_limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_limit = self.path_limits.get(str(scope.get("path", "")), self.max_bytes)

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
            if declared_length > request_limit:
                await self._send_too_large(scope, receive, send)
                return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > request_limit:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestBodyTooLarge:
            if response_started:
                raise RuntimeError("request body limit was exceeded after response start") from None
            await self._send_too_large(scope, receive, send)

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
