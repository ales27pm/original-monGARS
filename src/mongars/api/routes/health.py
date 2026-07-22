from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mongars.config import Settings
from mongars.db.session import Database
from mongars.inference.base import InferenceBackend
from mongars.web_search import SearxNGSearchBackend, WebSearchHealthStatus

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    settings = cast(Settings, request.app.state.settings)
    database = cast(Database, request.app.state.database)
    inference = cast(InferenceBackend, request.app.state.inference)
    web_search = cast(
        SearxNGSearchBackend | None,
        getattr(request.app.state, "web_search", None),
    )

    async def database_probe() -> dict[str, Any]:
        try:
            async with asyncio.timeout(settings.inference_health_timeout_seconds):
                await database.ping()
        except Exception as exc:  # readiness must normalize dependency failures
            return {"healthy": False, "error": type(exc).__name__}
        return {"healthy": True}

    async def web_search_probe() -> WebSearchHealthStatus:
        if not settings.web_search_enabled:
            return WebSearchHealthStatus(
                enabled=False,
                healthy=True,
                latency_ms=0.0,
            )
        if web_search is None:
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=0.0,
                error_code="not_configured",
            )
        try:
            async with asyncio.timeout(settings.web_search_timeout_seconds):
                return await web_search.health()
        except Exception as exc:  # readiness must normalize dependency failures
            return WebSearchHealthStatus(
                enabled=True,
                healthy=False,
                latency_ms=0.0,
                error_code=("timeout" if isinstance(exc, TimeoutError) else "unexpected_error"),
            )

    database_status, inference_status, web_search_status = await asyncio.gather(
        database_probe(),
        inference.health(),
        web_search_probe(),
    )
    body = {
        "status": "ready"
        if (
            database_status["healthy"]
            and inference_status.healthy
            and web_search_status.healthy
            and (not settings.web_search_enabled or web_search_status.enabled)
        )
        else "not_ready",
        "dependencies": {
            "database": database_status,
            "inference": {
                "backend": inference_status.backend,
                "healthy": inference_status.healthy,
                "backend_reachable": inference_status.backend_reachable,
                "chat_model_ready": inference_status.chat_model_ready,
                "embedding_model_ready": inference_status.embedding_model_ready,
                "latency_ms": round(inference_status.latency_ms, 2),
                "error_code": inference_status.error_code,
            },
            "web_search": {
                "enabled": settings.web_search_enabled,
                "healthy": web_search_status.healthy,
                "latency_ms": round(web_search_status.latency_ms, 2),
                "error_code": web_search_status.error_code,
            },
        },
    }
    response_status = (
        status.HTTP_200_OK if body["status"] == "ready" else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=response_status, content=body)
