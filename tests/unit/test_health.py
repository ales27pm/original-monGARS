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
    assert payload["dependencies"]["p2p"] == {
        "enabled": False,
        "healthy": True,
        "error_code": None,
    }


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


@pytest.mark.asyncio
async def test_readiness_requires_enabled_p2p_to_be_configured() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
        p2p_readiness_enabled=True,
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

    assert readiness.status_code == 503
    assert readiness.json()["status"] == "not_ready"
    assert readiness.json()["dependencies"]["p2p"] == {
        "enabled": True,
        "healthy": False,
        "error_code": "not_configured",
    }


@pytest.mark.asyncio
async def test_readiness_reports_disabled_evolution_scheduler_dependency() -> None:
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
    application.state.database = HealthyDatabase()
    application.state.inference = inference
    application.state.embeddings = None
    application.include_router(health.router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        readiness = await client.get("/v1/readyz", headers=_AUTH_HEADERS)

    assert readiness.status_code == 503
    assert readiness.json()["dependencies"]["evolution_scheduler"] == {
        "enabled": False,
        "status": "disabled",
        "healthy": True,
        "reason": "disabled_by_default",
        "can_run": False,
        "budgets": {
            "cpu_percent": 30,
            "memory_megabytes": 2048,
            "wall_clock_seconds": 45,
            "database_row_budget": 500,
            "proposal_count_budget": 25,
            "storage_bytes": 20_000_000,
            "proposal_cooldown_minutes": 30,
            "allow_network": False,
        },
    }


@pytest.mark.asyncio
async def test_readiness_reports_disabled_model_governance_dependency() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
        model_evolution_enabled=False,
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
    assert readiness.json()["dependencies"]["model_governance"] == {
        "enabled": False,
        "status": "disabled",
        "healthy": True,
        "reason": "disabled_by_default",
        "candidate_registry": {
            "active_alias": None,
            "active_digest": None,
            "active_generation": None,
            "prior_generation_anchor": None,
            "rollback_target_alias": None,
            "rollback_target_digest": None,
        },
        "benchmarks": {
            "scoring_policy_version": None,
            "benchmarking_policy_version": None,
            "minimum_sample_size": None,
            "promotion_quality_threshold": None,
            "rollback_quality_threshold": None,
        },
    }


@pytest.mark.asyncio
async def test_readiness_reports_blocked_model_governance_when_active_digest_is_missing() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
        model_evolution_enabled=True,
        model_evolution_active_chat_digest=None,
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

    assert readiness.status_code == 503
    assert readiness.json()["status"] == "not_ready"
    governance = readiness.json()["dependencies"]["model_governance"]
    assert governance["enabled"] is True
    assert governance["status"] == "blocked"
    assert governance["healthy"] is False
    assert governance["reason"] == "active_model_not_fully_configured"
    assert governance["candidate_registry"]["active_alias"] == "qwen3:4b-instruct"
    assert governance["candidate_registry"]["active_generation"] == 1


@pytest.mark.asyncio
async def test_readiness_reports_ready_model_governance_with_full_configuration() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
        model_evolution_enabled=True,
        model_evolution_active_chat_digest="f" * 64,
        model_evolution_scoring_policy_version="bench-v2",
        model_evolution_benchmarking_policy_version="suite-v1",
        model_evolution_minimum_sample_size=500,
        model_evolution_promotion_quality_threshold=0.91,
        model_evolution_rollback_quality_threshold=0.81,
        model_evolution_last_rollback_target_alias="rollback-candidate",
        model_evolution_last_rollback_target_digest="e" * 64,
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
    governance = readiness.json()["dependencies"]["model_governance"]
    assert governance["enabled"] is True
    assert governance["status"] == "ready"
    assert governance["healthy"] is True
    assert governance["reason"] is None
    assert governance["candidate_registry"]["active_alias"] == "qwen3:4b-instruct"
    assert governance["candidate_registry"]["active_digest"] == "f" * 64
    assert governance["candidate_registry"]["active_generation"] == 1
    assert governance["candidate_registry"]["rollback_target_alias"] == "rollback-candidate"
    assert governance["candidate_registry"]["rollback_target_digest"] == "e" * 64
    assert governance["benchmarks"]["scoring_policy_version"] == "bench-v2"
    assert governance["benchmarks"]["benchmarking_policy_version"] == "suite-v1"


@pytest.mark.asyncio
async def test_readiness_reports_disabled_executor_security_dependency() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
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
    assert readiness.json()["status"] == "ready"
    assert readiness.json()["dependencies"]["executor_security"] == {
        "enabled": False,
        "status": "disabled_by_default",
        "healthy": True,
        "reason": "executor security review not yet approved",
        "approved_kinds": [
            "evolution.proposal.generate",
            "evolution.proposal.execute",
            "execution.sandbox.echo",
        ],
        "requires_approval": True,
    }


@pytest.mark.asyncio
async def test_readiness_reports_enabled_executor_security_dependency_after_review() -> None:
    inference = ReadinessInference(
        HealthStatus(
            backend="ollama",
            backend_reachable=True,
            chat_model_ready=True,
            embedding_model_ready=True,
            latency_ms=1.0,
        )
    )
    database, embeddings = _healthy_runtime()
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        web_search_enabled=False,
        executor_security_review_approved=True,
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
    assert readiness.json()["status"] == "ready"
    assert readiness.json()["dependencies"]["executor_security"] == {
        "enabled": True,
        "status": "ready",
        "healthy": True,
        "reason": None,
        "approved_kinds": [
            "evolution.proposal.generate",
            "evolution.proposal.execute",
            "execution.sandbox.echo",
        ],
        "requires_approval": True,
    }
