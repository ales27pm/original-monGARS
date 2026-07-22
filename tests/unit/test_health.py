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
    application.state.settings = Settings(environment=Environment.TEST)
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
