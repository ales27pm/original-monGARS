from __future__ import annotations

from collections.abc import Mapping, Sequence

import httpx
import pytest
from fastapi import FastAPI

from mongars.api.routes import health
from mongars.config import Environment, Settings
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    EmbeddingResponse,
    HealthStatus,
    JsonValue,
)
from mongars.web_search import SearxNGSearchBackend


class HealthyDatabase:
    async def ping(self) -> None:
        return None


class ReadinessInference:
    def __init__(self, status: HealthStatus) -> None:
        self.status = status

    async def health(self) -> HealthStatus:
        return self.status

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        options: Mapping[str, JsonValue] | None = None,
    ) -> ChatResponse:
        raise NotImplementedError

    async def embed(
        self,
        inputs: Sequence[str],
        *,
        model: str | None = None,
        expected_dimension: int | None = None,
    ) -> EmbeddingResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_readiness_requires_each_configured_model_but_liveness_does_not() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=False,
            latency_ms=1.25,
            error_code="required_models_missing",
        )
    )
    application = FastAPI()
    application.state.settings = Settings(
        environment=Environment.TEST,
        web_search_enabled=False,
    )
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        liveness = await client.get("/v1/healthz")
        readiness = await client.get("/v1/readyz")

    assert liveness.status_code == 200
    assert liveness.json() == {"status": "ok"}
    assert readiness.status_code == 503
    assert readiness.json()["dependencies"]["inference"] == {
        "backend": "ollama",
        "healthy": False,
        "backend_reachable": True,
        "chat_model_ready": True,
        "embedding_model_ready": False,
        "latency_ms": 1.25,
        "error_code": "required_models_missing",
    }
    assert readiness.json()["dependencies"]["web_search"] == {
        "enabled": False,
        "healthy": True,
        "latency_ms": 0.0,
        "error_code": None,
    }


@pytest.mark.asyncio
async def test_readiness_requires_enabled_web_search_to_be_configured() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    application = FastAPI()
    application.state.settings = Settings(
        environment=Environment.TEST,
        web_search_enabled=True,
    )
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.state.web_search = None
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/v1/readyz")

    assert readiness.status_code == 503
    assert readiness.json()["status"] == "not_ready"
    assert readiness.json()["dependencies"]["web_search"] == {
        "enabled": True,
        "healthy": False,
        "latency_ms": 0.0,
        "error_code": "not_configured",
    }


@pytest.mark.asyncio
async def test_readiness_reports_healthy_non_query_web_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/config"
        assert "q" not in request.url.params
        return httpx.Response(200, json={"instance_name": "monGARS Search"})

    search_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    web_search = SearxNGSearchBackend(
        base_url="https://search.example.com",
        client=search_client,
    )
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    application = FastAPI()
    application.state.settings = Settings(
        environment=Environment.TEST,
        web_search_enabled=True,
    )
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.state.web_search = web_search
    application.include_router(health.router)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            readiness = await client.get("/v1/readyz")
    finally:
        await search_client.aclose()

    payload = readiness.json()
    assert readiness.status_code == 200
    assert payload["status"] == "ready"
    assert payload["dependencies"]["web_search"]["enabled"] is True
    assert payload["dependencies"]["web_search"]["healthy"] is True
    assert payload["dependencies"]["web_search"]["latency_ms"] >= 0
    assert payload["dependencies"]["web_search"]["error_code"] is None
