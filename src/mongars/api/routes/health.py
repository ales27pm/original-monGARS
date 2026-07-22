from __future__ import annotations

import asyncio
from typing import Any, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from mongars.api.dependencies import PrincipalDependency
from mongars.config import Settings
from mongars.db.session import Database
from mongars.embeddings.service import EmbeddingService
from mongars.inference.base import InferenceBackend
from mongars.runtime import (
    DurableRuntimeReadiness,
    RuntimeHeartbeatRepository,
    RuntimeReadinessService,
    unavailable_runtime_readiness,
)
from mongars.web_search import SearxNGSearchBackend, WebSearchHealthStatus

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, _principal: PrincipalDependency) -> JSONResponse:
    settings = cast(Settings, request.app.state.settings)
    database = cast(Database, request.app.state.database)
    inference = cast(InferenceBackend, request.app.state.inference)
    embeddings = cast(
        EmbeddingService | None,
        getattr(request.app.state, "embeddings", None),
    )
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

    async def durable_runtime_probe() -> DurableRuntimeReadiness:
        active_space = embeddings.embedding_space if embeddings is not None else None
        embedding_error_code: str | None = None
        if embeddings is None:
            embedding_error_code = "embedding_service_not_configured"
        elif active_space is None:
            try:
                async with asyncio.timeout(settings.inference_health_timeout_seconds):
                    active_space = await embeddings.resolve_space()
            except TimeoutError:
                embedding_error_code = "embedding_timeout"
            except Exception as exc:  # readiness exposes codes, never provider details
                raw_code = getattr(exc, "code", None)
                embedding_error_code = (
                    raw_code if isinstance(raw_code, str) else "embedding_space_unresolved"
                )
        try:
            async with database.session_factory() as session:
                return await RuntimeReadinessService(RuntimeHeartbeatRepository(session)).inspect(
                    active_space=active_space,
                    embedding_error_code=embedding_error_code,
                    owner_id=settings.owner_id,
                    stale_seconds=settings.worker_runtime_stale_seconds,
                )
        except Exception as exc:  # readiness must normalize dependency failures
            return unavailable_runtime_readiness(error_code=type(exc).__name__)

    database_status, inference_status, web_search_status, runtime_status = await asyncio.gather(
        database_probe(),
        inference.health(),
        web_search_probe(),
        durable_runtime_probe(),
    )
    body = {
        "status": "ready"
        if (
            database_status["healthy"]
            and inference_status.healthy
            and web_search_status.healthy
            and (not settings.web_search_enabled or web_search_status.enabled)
            and runtime_status.healthy
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
            "worker": {
                "healthy": runtime_status.worker.healthy,
                "status": runtime_status.worker.status,
                "component_id": runtime_status.worker.component_id,
                "instance_id": (
                    str(runtime_status.worker.instance_id)
                    if runtime_status.worker.instance_id is not None
                    else None
                ),
                "version": runtime_status.worker.version,
                "git_sha": runtime_status.worker.git_sha,
                "last_seen_at": (
                    runtime_status.worker.last_seen_at.isoformat()
                    if runtime_status.worker.last_seen_at is not None
                    else None
                ),
                "age_seconds": (
                    round(runtime_status.worker.age_seconds, 2)
                    if runtime_status.worker.age_seconds is not None
                    else None
                ),
                "error_code": runtime_status.worker.error_code,
            },
            "parser": {
                "healthy": runtime_status.parser.healthy,
                "version": runtime_status.parser.version,
                "error_code": runtime_status.parser.error_code,
            },
            "embedding_space": {
                "healthy": runtime_status.embedding_space.healthy,
                "status": runtime_status.embedding_space.status,
                "space_id": (
                    runtime_status.embedding_space.active_space.space_id
                    if runtime_status.embedding_space.active_space is not None
                    else None
                ),
                "model_alias": (
                    runtime_status.embedding_space.active_space.model_alias
                    if runtime_status.embedding_space.active_space is not None
                    else None
                ),
                "model_digest": (
                    runtime_status.embedding_space.active_space.model_digest
                    if runtime_status.embedding_space.active_space is not None
                    else None
                ),
                "dimension": (
                    runtime_status.embedding_space.active_space.dimension
                    if runtime_status.embedding_space.active_space is not None
                    else None
                ),
                "worker_space_id": runtime_status.embedding_space.worker_space_id,
                "total_chunk_count": runtime_status.embedding_space.total_chunk_count,
                "compatible_chunk_count": runtime_status.embedding_space.compatible_chunk_count,
                "legacy_chunk_count": runtime_status.embedding_space.legacy_chunk_count,
                "reindex_required": runtime_status.embedding_space.reindex_required,
                "error_code": runtime_status.embedding_space.error_code,
            },
        },
    }
    response_status = (
        status.HTTP_200_OK if body["status"] == "ready" else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=response_status, content=body)
