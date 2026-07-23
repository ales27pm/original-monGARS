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
from mongars.evolution.governance import ModelGovernanceService
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

    async def p2p_probe() -> dict[str, bool | None | str]:
        if not settings.p2p_readiness_enabled:
            return {"healthy": True, "error_code": None}
        if getattr(request.app.state, "p2p", None) is None:
            return {
                "healthy": False,
                "error_code": "not_configured",
            }
        return {"healthy": True, "error_code": None}

    async def model_governance_probe() -> dict[str, Any]:
        if not settings.model_evolution_enabled:
            return _model_governance_dependency(settings=settings)
        try:
            async with database.session_factory() as session:
                return await ModelGovernanceService(
                    session=session, settings=settings
                ).dependency_payload(settings.owner_id)
        except Exception:
            return _model_governance_dependency(settings=settings)

    database_status, inference_status, web_search_status, runtime_status, p2p_status, model_governance_status, executor_security_status = await asyncio.gather(
        database_probe(),
        inference.health(),
        web_search_probe(),
        durable_runtime_probe(),
        p2p_probe(),
        model_governance_probe(),
        asyncio.to_thread(_executor_security_dependency, settings=settings),
    )
    body = {
        "status": "ready"
        if (
            database_status["healthy"]
            and inference_status.healthy
            and web_search_status.healthy
            and (not settings.web_search_enabled or web_search_status.enabled)
            and runtime_status.healthy
            and model_governance_status["healthy"]
            and executor_security_status["healthy"]
            and (not settings.p2p_readiness_enabled or p2p_status["healthy"])
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
            "evolution_scheduler": _evolution_scheduler_dependency(
                worker_capabilities=runtime_status.worker.capabilities,
                settings=settings,
            ),
            "p2p": {
                "enabled": settings.p2p_readiness_enabled,
                "healthy": p2p_status["healthy"],
                "error_code": p2p_status["error_code"],
            },
            "model_governance": {
                "enabled": model_governance_status["enabled"],
                "status": model_governance_status["status"],
                "healthy": model_governance_status["healthy"],
                "reason": model_governance_status["reason"],
                "candidate_registry": model_governance_status["candidate_registry"],
                "benchmarks": model_governance_status["benchmarks"],
            },
            "executor_security": {
                "enabled": executor_security_status["enabled"],
                "status": executor_security_status["status"],
                "healthy": executor_security_status["healthy"],
                "reason": executor_security_status["reason"],
                "approved_kinds": executor_security_status["approved_kinds"],
                "requires_approval": executor_security_status["requires_approval"],
            },
        },
    }
    response_status = (
        status.HTTP_200_OK if body["status"] == "ready" else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=response_status, content=body)


def _evolution_scheduler_dependency(
    *,
    worker_capabilities: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    if not settings.evolution_scheduler_enabled:
        return {
            "enabled": False,
            "status": "disabled",
            "healthy": True,
            "reason": "disabled_by_default",
            "can_run": False,
            "budgets": {
                "cpu_percent": settings.evolution_scheduler_cpu_percent_cap,
                "memory_megabytes": settings.evolution_scheduler_memory_megabytes_cap,
                "wall_clock_seconds": settings.evolution_scheduler_wall_clock_seconds,
                "database_row_budget": settings.evolution_scheduler_database_row_budget,
                "proposal_count_budget": settings.evolution_scheduler_proposal_count_budget,
                "storage_bytes": settings.evolution_scheduler_storage_budget_bytes,
                "proposal_cooldown_minutes": settings.evolution_scheduler_cooldown_minutes,
                "allow_network": settings.evolution_scheduler_allow_network,
            },
        }

    if not worker_capabilities:
        return {
            "enabled": settings.evolution_scheduler_enabled,
            "status": "unknown",
            "healthy": True,
            "reason": "runtime_capability_not_available",
            "can_run": False,
            "budgets": {
                "cpu_percent": settings.evolution_scheduler_cpu_percent_cap,
                "memory_megabytes": settings.evolution_scheduler_memory_megabytes_cap,
                "wall_clock_seconds": settings.evolution_scheduler_wall_clock_seconds,
                "database_row_budget": settings.evolution_scheduler_database_row_budget,
                "proposal_count_budget": settings.evolution_scheduler_proposal_count_budget,
                "storage_bytes": settings.evolution_scheduler_storage_budget_bytes,
                "proposal_cooldown_minutes": settings.evolution_scheduler_cooldown_minutes,
                "allow_network": settings.evolution_scheduler_allow_network,
            },
        }

    scheduler = worker_capabilities.get("evolution_scheduler")
    if not isinstance(scheduler, dict):
        return {
            "enabled": settings.evolution_scheduler_enabled,
            "status": "unavailable",
            "healthy": True,
            "reason": "scheduler_capability_missing",
            "can_run": False,
            "budgets": {
                "cpu_percent": settings.evolution_scheduler_cpu_percent_cap,
                "memory_megabytes": settings.evolution_scheduler_memory_megabytes_cap,
                "wall_clock_seconds": settings.evolution_scheduler_wall_clock_seconds,
                "database_row_budget": settings.evolution_scheduler_database_row_budget,
                "proposal_count_budget": settings.evolution_scheduler_proposal_count_budget,
                "storage_bytes": settings.evolution_scheduler_storage_budget_bytes,
                "proposal_cooldown_minutes": settings.evolution_scheduler_cooldown_minutes,
                "allow_network": settings.evolution_scheduler_allow_network,
            },
        }
    return {
        "enabled": bool(scheduler.get("enabled")),
        "status": scheduler.get("status", "unknown"),
        "healthy": not settings.evolution_scheduler_enabled or scheduler.get("status") != "blocked",
        "reason": scheduler.get("reason"),
        "can_run": bool(scheduler.get("can_run", False)),
        "budgets": scheduler.get("budgets")
        if isinstance(scheduler.get("budgets"), dict)
        else {},
    }


def _model_governance_dependency(
    *,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.model_evolution_enabled:
        return {
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

    active_alias = settings.model_evolution_active_chat_alias
    active_digest = settings.model_evolution_active_chat_digest
    ready = bool(active_alias and active_digest)
    return {
        "enabled": True,
        "status": "ready" if ready else "blocked",
        "healthy": ready,
        "reason": None if ready else "active_model_not_fully_configured",
        "candidate_registry": {
            "active_alias": active_alias,
            "active_digest": active_digest,
            "active_generation": settings.model_evolution_active_generation,
            "prior_generation_anchor": settings.model_evolution_prior_generation_anchor,
            "rollback_target_alias": settings.model_evolution_last_rollback_target_alias,
            "rollback_target_digest": settings.model_evolution_last_rollback_target_digest,
        },
        "benchmarks": {
            "scoring_policy_version": settings.model_evolution_scoring_policy_version,
            "benchmarking_policy_version": settings.model_evolution_benchmarking_policy_version,
            "minimum_sample_size": settings.model_evolution_minimum_sample_size,
            "promotion_quality_threshold": settings.model_evolution_promotion_quality_threshold,
            "rollback_quality_threshold": settings.model_evolution_rollback_quality_threshold,
        },
    }


def _executor_security_dependency(
    *,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.executor_security_review_approved:
        return {
            "enabled": False,
            "status": "disabled_by_default",
            "healthy": True,
            "reason": "executor security review not yet approved",
            "approved_kinds": (
                "evolution.proposal.generate",
                "evolution.proposal.execute",
                "execution.sandbox.echo",
            ),
            "requires_approval": True,
        }

    return {
        "enabled": True,
        "status": "ready",
        "healthy": True,
        "reason": None,
        "approved_kinds": (
            "evolution.proposal.generate",
            "evolution.proposal.execute",
            "execution.sandbox.echo",
        ),
        "requires_approval": True,
    }
