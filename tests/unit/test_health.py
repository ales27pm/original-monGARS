from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from mongars.api.routes import health
from mongars.config import Environment, Settings
from mongars.db.models import RuntimeComponent
from mongars.embeddings.models import EmbeddingProfile, EmbeddingSpace
from mongars.inference import (
    ChatMessage,
    ChatResponse,
    HealthStatus,
    JsonValue,
)
from mongars.security.auth import BearerTokenAuth
from mongars.web_search import SearxNGSearchBackend

_AUTH_VALUE = "unit-readiness-token"
_AUTH_HEADERS = {"Authorization": f"Bearer {_AUTH_VALUE}"}


def _configure_auth(application: FastAPI, settings: Settings) -> None:
    application.state.settings = settings
    application.state.auth = BearerTokenAuth(settings, subject=settings.owner_id)


class HealthyDatabase:
    async def ping(self) -> None:
        return None


class RuntimeSession:
    def __init__(self, component: RuntimeComponent) -> None:
        self._component = component
        self._calls = 0

    async def scalar(self, _statement: object) -> object:
        self._calls += 1
        return self._component if self._calls == 1 else 0


class HealthyRuntimeDatabase(HealthyDatabase):
    def __init__(
        self,
        component: RuntimeComponent,
        events: list[str] | None = None,
    ) -> None:
        self._component = component
        self._events = events

    @property
    def component(self) -> RuntimeComponent:
        return self._component

    @asynccontextmanager
    async def session_factory(self) -> Any:
        if self._events is not None:
            self._events.append("database_session")
        yield RuntimeSession(self._component)


class ResolvedEmbeddings:
    def __init__(self, space: EmbeddingSpace) -> None:
        self.embedding_space = space

    async def resolve_space(self) -> EmbeddingSpace:
        raise AssertionError("readiness must reuse the already resolved embedding space")


class ResolvingEmbeddings:
    def __init__(self, space: EmbeddingSpace, events: list[str]) -> None:
        self.embedding_space: EmbeddingSpace | None = None
        self._space = space
        self._events = events

    async def resolve_space(self) -> EmbeddingSpace:
        self._events.append("resolve_embedding_space")
        self.embedding_space = self._space
        return self._space


def _healthy_runtime() -> tuple[HealthyRuntimeDatabase, ResolvedEmbeddings]:
    space = EmbeddingSpace.from_profile(
        provider="ollama",
        model_alias="nomic-embed-text",
        model_digest="a" * 64,
        dimension=768,
        normalization_policy="none",
        maximum_input_bytes=32_000,
        profile=EmbeddingProfile(),
    )
    component = RuntimeComponent(
        component_id="worker:primary",
        instance_id=uuid4(),
        component_type="worker",
        version="0.1.0",
        git_sha="abc123",
        status="healthy",
        capabilities={
            "document_parser": {
                "healthy": True,
                "parser_version": "pypdf-6.0",
                "error_code": None,
            },
            "embedding_space": {
                "space_id": space.space_id,
                "model_alias": space.model_alias,
                "model_digest": space.model_digest,
                "dimension": space.dimension,
            },
        },
        last_seen_at=datetime.now(UTC),
    )
    return HealthyRuntimeDatabase(component), ResolvedEmbeddings(space)


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
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
    )
    _configure_auth(application, settings)
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        liveness = await client.get("/v1/healthz")
        anonymous_readiness = await client.get("/v1/readyz")
        invalid_readiness = await client.get(
            "/v1/readyz",
            headers={"Authorization": "Bearer invalid-readiness-token"},
        )
        readiness = await client.get("/v1/readyz", headers=_AUTH_HEADERS)

    assert liveness.status_code == 200
    assert liveness.json() == {"status": "ok"}
    assert anonymous_readiness.status_code == 401
    assert anonymous_readiness.headers["www-authenticate"] == "Bearer"
    assert anonymous_readiness.json() == {"detail": "Invalid or missing bearer token"}
    assert invalid_readiness.status_code == 401
    assert invalid_readiness.json() == {"detail": "Invalid or missing bearer token"}
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
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=True,
    )
    _configure_auth(application, settings)
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.state.web_search = None
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/v1/readyz", headers=_AUTH_HEADERS)

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
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=True,
    )
    _configure_auth(application, settings)
    database, embeddings = _healthy_runtime()
    application.state.database = database
    application.state.inference = inference
    application.state.embeddings = embeddings
    application.state.web_search = web_search
    application.include_router(health.router)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            readiness = await client.get("/v1/readyz", headers=_AUTH_HEADERS)
    finally:
        await search_client.aclose()

    payload = readiness.json()
    assert readiness.status_code == 200
    assert payload["status"] == "ready"
    assert payload["dependencies"]["web_search"]["enabled"] is True
    assert payload["dependencies"]["web_search"]["healthy"] is True
    assert payload["dependencies"]["web_search"]["latency_ms"] >= 0
    assert payload["dependencies"]["web_search"]["error_code"] is None
    assert payload["dependencies"]["worker"]["healthy"] is True
    assert payload["dependencies"]["worker"]["status"] == "healthy"
    assert payload["dependencies"]["parser"] == {
        "healthy": True,
        "version": "pypdf-6.0",
        "error_code": None,
    }
    embedding_status = payload["dependencies"]["embedding_space"]
    assert embedding_status["healthy"] is True
    assert embedding_status["status"] == "ready"
    assert embedding_status["space_id"] == embeddings.embedding_space.space_id
    assert embedding_status["model_alias"] == "nomic-embed-text"
    assert embedding_status["model_digest"] == "a" * 64
    assert embedding_status["dimension"] == 768
    assert embedding_status["total_chunk_count"] == 0
    assert embedding_status["compatible_chunk_count"] == 0
    assert embedding_status["legacy_chunk_count"] == 0
    assert embedding_status["reindex_required"] is False


@pytest.mark.asyncio
async def test_readiness_resolves_embedding_identity_before_opening_database_session() -> None:
    base_database, resolved_embeddings = _healthy_runtime()
    events: list[str] = []
    database = HealthyRuntimeDatabase(base_database.component, events)
    embeddings = ResolvingEmbeddings(resolved_embeddings.embedding_space, events)
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
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
    )
    _configure_auth(application, settings)
    application.state.database = database
    application.state.inference = inference
    application.state.embeddings = embeddings
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/v1/readyz", headers=_AUTH_HEADERS)

    assert readiness.status_code == 200
    assert events == ["resolve_embedding_space", "database_session"]
