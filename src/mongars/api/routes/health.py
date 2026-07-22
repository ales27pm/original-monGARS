from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mongars.config import Settings
from mongars.db.session import Database
from mongars.inference.base import InferenceBackend

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    settings = cast(Settings, request.app.state.settings)
    database = cast(Database, request.app.state.database)
    inference = cast(InferenceBackend, request.app.state.inference)

    async def database_probe() -> dict[str, Any]:
        try:
            async with asyncio.timeout(settings.inference_health_timeout_seconds):
                await database.ping()
        except Exception as exc:  # readiness must normalize dependency failures
            return {"healthy": False, "error": type(exc).__name__}
        return {"healthy": True}

    database_status, inference_status = await asyncio.gather(database_probe(), inference.health())
    body = {
        "status": "ready"
        if database_status["healthy"] and inference_status.healthy
        else "not_ready",
        "dependencies": {
            "database": database_status,
            "inference": {
                "backend": inference_status.backend,
                "healthy": inference_status.healthy,
                "latency_ms": round(inference_status.latency_ms, 2),
                "error_code": inference_status.error_code,
            },
        },
    }
    response_status = (
        status.HTTP_200_OK if body["status"] == "ready" else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=response_status, content=body)
