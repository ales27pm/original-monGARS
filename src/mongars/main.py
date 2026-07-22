from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response

from mongars.api.routes import chat, health, memory, tasks
from mongars.config import Environment, Settings, get_settings
from mongars.db.session import Database
from mongars.http import RequestBodyLimitMiddleware
from mongars.inference.base import InferenceBackend
from mongars.inference.ollama import OllamaBackend
from mongars.logging import configure_logging
from mongars.security.auth import BearerTokenAuth
from mongars.security.policy import ActionClassification, ToolPolicy

logger = logging.getLogger(__name__)


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    inference: InferenceBackend | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    runtime_database = database or Database(runtime_settings)
    runtime_inference = inference or OllamaBackend(
        base_url=runtime_settings.ollama_base_url,
        chat_model=runtime_settings.ollama_chat_model,
        embedding_model=runtime_settings.ollama_embedding_model,
        embedding_dimension=runtime_settings.embedding_dimensions,
        think=runtime_settings.ollama_think,
        timeout=runtime_settings.inference_timeout_seconds,
        health_timeout=runtime_settings.inference_health_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        await runtime_inference.aclose()
        await runtime_database.close()

    production = runtime_settings.environment is Environment.PRODUCTION
    application = FastAPI(
        title="monGARS Control Plane",
        version="0.1.0",
        docs_url=None if production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.settings = runtime_settings
    application.state.database = runtime_database
    application.state.inference = runtime_inference
    application.state.auth = BearerTokenAuth(runtime_settings, subject=runtime_settings.owner_id)
    application.state.policy = ToolPolicy(
        {
            ("memory", "search"): ActionClassification.READ_ONLY,
            ("memory", "note.create"): ActionClassification.LOCAL_MUTATION,
        }
    )

    application.add_middleware(
        RequestBodyLimitMiddleware,
        max_bytes=runtime_settings.max_request_bytes,
    )
    if runtime_settings.cors_origins:
        # CORS must wrap the body limiter so browser clients can observe 4xx boundary
        # responses. TrustedHost remains outside both in production.
        application.add_middleware(
            CORSMiddleware,
            allow_origins=runtime_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
    if production:
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=runtime_settings.trusted_hosts,
        )

    @application.middleware("http")
    async def request_boundary(request: Request, call_next: object) -> Response:
        started = time.monotonic()
        response = await call_next(request)  # type: ignore[operator]
        duration_ms = (time.monotonic() - started) * 1000
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        logger.info(
            "request_complete",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return cast(Response, response)

    application.include_router(health.router)
    application.include_router(chat.router)
    application.include_router(tasks.router)
    application.include_router(memory.router)
    return application


app = create_app()


def run() -> None:
    uvicorn.run("mongars.main:app", host="127.0.0.1", port=8000, proxy_headers=False)


if __name__ == "__main__":
    run()
