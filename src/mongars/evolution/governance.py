"""Durable model-governance state and task execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.config import Settings
from mongars.db.models import (
    ModelActivationHistory,
    ModelBenchmarkRun,
    ModelBenchmarkSuite,
    ModelCandidate,
    ModelGovernanceState,
    ModelPromotionProposal,
)
from mongars.rm.contracts import (
    BenchmarkRunPayload,
    BenchmarkSuiteCreatePayload,
    ModelActivationPayload,
    ModelCandidateRegisterPayload,
    ModelRollbackPayload,
    PromotionProposalPayload,
)


class ModelGovernanceError(RuntimeError):
    """Base error for model-governance orchestration failures."""


class ModelGovernanceConflict(ModelGovernanceError):
    """Raised when mutable requests conflict with existing governance rows."""


def _model_governance_dependency(settings: Settings) -> dict[str, Any]:
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



class ModelGovernanceDisabled(ModelGovernanceError):
    """Raised when model-governance operations are disabled."""


def _validate_owner_id(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("owner_id must be a non-empty canonical string")
    if len(value) > 255:
        raise ValueError("owner_id exceeds the persistence limit")
    return value


def model_governance_dependency_payload(
    *,
    settings: Settings,
    state: ModelGovernanceState | None,
) -> dict[str, Any]:
    """Map active model-governance state into the health dependency contract."""
    if not settings.model_evolution_enabled:
        return _model_governance_dependency(settings=settings)

    active_alias = (state.active_chat_alias if state is not None else None) or (
        settings.model_evolution_active_chat_alias
    )
    active_digest = (state.active_chat_digest if state is not None else None) or (
        settings.model_evolution_active_chat_digest
    )
    active_generation = state.active_generation if state is not None else (
        settings.model_evolution_active_generation
    )
    prior_generation_anchor = (
        state.prior_generation_anchor
        if state is not None and state.prior_generation_anchor is not None
        else settings.model_evolution_prior_generation_anchor
    )
    rollback_alias = (
        state.rollback_target_alias if state is not None else None
    ) or settings.model_evolution_last_rollback_target_alias
    rollback_digest = (
        state.rollback_target_digest if state is not None else None
    ) or settings.model_evolution_last_rollback_target_digest
    scoring_policy_version = (
        state.scoring_policy_version if state is not None else None
    ) or settings.model_evolution_scoring_policy_version
    benchmarking_policy_version = (
        state.benchmarking_policy_version if state is not None else None
    ) or settings.model_evolution_benchmarking_policy_version
    minimum_sample_size = (
        state.minimum_sample_size if state is not None else None
    ) or settings.model_evolution_minimum_sample_size
    promotion_quality_threshold = (
        state.promotion_quality_threshold if state is not None else None
    ) or settings.model_evolution_promotion_quality_threshold
    rollback_quality_threshold = (
        state.rollback_quality_threshold if state is not None else None
    ) or settings.model_evolution_rollback_quality_threshold

    ready = bool(active_alias and active_digest)
    return {
        "enabled": True,
        "status": "ready" if ready else "blocked",
        "healthy": ready,
        "reason": None if ready else "active_model_not_fully_configured",
        "candidate_registry": {
            "active_alias": active_alias,
            "active_digest": active_digest,
            "active_generation": active_generation,
            "prior_generation_anchor": prior_generation_anchor,
            "rollback_target_alias": rollback_alias,
            "rollback_target_digest": rollback_digest,
        },
        "benchmarks": {
            "scoring_policy_version": scoring_policy_version,
            "benchmarking_policy_version": benchmarking_policy_version,
            "minimum_sample_size": minimum_sample_size,
            "promotion_quality_threshold": promotion_quality_threshold,
            "rollback_quality_threshold": rollback_quality_threshold,
        },
    }


class ModelGovernanceService:
    """Mutable model-governance operations for tasks and runtime probes."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def dependency_payload(self, owner_id: str) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        state = await self._get_state(owner_id=owner)
        return model_governance_dependency_payload(settings=self._settings, state=state)

    async def resolve_active_chat_model(self, owner_id: str) -> tuple[str, str | None]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            return self._settings.ollama_chat_model, self._settings.model_evolution_active_chat_digest

        state = await self._get_state(owner_id=owner)
        active_alias = (state.active_chat_alias if state is not None else None)
        if not active_alias:
            active_alias = self._settings.model_evolution_active_chat_alias
        active_digest = (state.active_chat_digest if state is not None else None)
        if not active_digest:
            active_digest = self._settings.model_evolution_active_chat_digest

        resolved_alias = (
            active_alias if isinstance(active_alias, str) and active_alias else self._settings.ollama_chat_model
        )
        return resolved_alias, active_digest

    async def register_candidate(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(ModelCandidateRegisterPayload, payload)
        existing = await self._session.get(
            ModelCandidate,
            {"owner_id": owner, "candidate_alias": payload_obj.candidate_alias},
        )
        if existing is not None:
            if existing.candidate_digest != payload_obj.candidate_digest:
                raise ModelGovernanceConflict("candidate alias already exists with a different digest")
            if existing.scoring_policy_version != payload_obj.scoring_policy_version:
                raise ModelGovernanceConflict(
                    "candidate alias already exists with a different policy version"
                )
            return {
                "candidate_alias": payload_obj.candidate_alias,
                "candidate_digest": payload_obj.candidate_digest,
                "created": False,
            }

        candidate = ModelCandidate(
            owner_id=owner,
            candidate_alias=payload_obj.candidate_alias,
            candidate_digest=payload_obj.candidate_digest,
            scoring_policy_version=payload_obj.scoring_policy_version,
            requested_by=payload_obj.requested_by,
            last_seen_at=datetime.now(UTC),
        )
        self._session.add(candidate)
        await self._session.flush()
        return {
            "candidate_alias": payload_obj.candidate_alias,
            "candidate_digest": payload_obj.candidate_digest,
            "created": True,
        }

    async def create_benchmark_suite(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(BenchmarkSuiteCreatePayload, payload)
        existing = await self._session.get(ModelBenchmarkSuite, {"owner_id": owner, "suite_id": payload_obj.suite_id})
        if existing is not None:
            if (
                existing.suite_version != payload_obj.suite_version
                or existing.scoring_policy_version != payload_obj.scoring_policy_version
                or existing.target_metrics != list(payload_obj.target_metrics)
                or existing.minimum_sample_size != payload_obj.minimum_sample_size
                or existing.regression_tolerance != payload_obj.regression_tolerance
            ):
                raise ModelGovernanceConflict("benchmark suite has changed since it was proposed")
            return {
                "suite_id": str(payload_obj.suite_id),
                "suite_version": payload_obj.suite_version,
                "created": False,
            }
        suite = ModelBenchmarkSuite(
            owner_id=owner,
            suite_id=payload_obj.suite_id,
            suite_version=payload_obj.suite_version,
            scoring_policy_version=payload_obj.scoring_policy_version,
            target_metrics=list(payload_obj.target_metrics),
            minimum_sample_size=payload_obj.minimum_sample_size,
            regression_tolerance=payload_obj.regression_tolerance,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(suite)
        await self._session.flush()
        return {
            "suite_id": str(payload_obj.suite_id),
            "suite_version": payload_obj.suite_version,
            "created": True,
        }

    async def record_benchmark_run(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(BenchmarkRunPayload, payload)
        suite = await self._session.get(
            ModelBenchmarkSuite,
            {"owner_id": owner, "suite_id": payload_obj.suite_id},
        )
        if suite is None:
            raise ModelGovernanceConflict("referenced benchmark suite is not registered")
        if suite.suite_version != payload_obj.suite_version:
            raise ModelGovernanceConflict("benchmark suite version does not match the referenced suite")

        candidate = await self._session.get(
            ModelCandidate,
            {"owner_id": owner, "candidate_alias": payload_obj.candidate_alias},
        )
        if candidate is None or candidate.candidate_digest != payload_obj.candidate_digest:
            raise ModelGovernanceConflict("benchmark candidate is not registered")

        state = await self._get_or_bootstrap_state(owner)
        if suite.scoring_policy_version != state.scoring_policy_version:
            raise ModelGovernanceConflict("benchmark suite policy differs from active governance policy")
        if payload_obj.sample_size < state.minimum_sample_size:
            raise ModelGovernanceConflict("benchmark sample size is below minimum configured threshold")

        existing = await self._session.get(ModelBenchmarkRun, {"owner_id": owner, "run_id": payload_obj.run_id})
        if existing is not None:
            if not _rows_match(ModelBenchmarkRunRecord.from_orm(existing), payload_obj):
                raise ModelGovernanceConflict("benchmark run payload changed after creation")
            return {
                "run_id": str(payload_obj.run_id),
                "created": False,
            }

        run = ModelBenchmarkRun(
            owner_id=owner,
            run_id=payload_obj.run_id,
            suite_id=payload_obj.suite_id,
            suite_version=payload_obj.suite_version,
            candidate_alias=payload_obj.candidate_alias,
            candidate_digest=payload_obj.candidate_digest,
            sample_size=payload_obj.sample_size,
            quality_score=payload_obj.quality_score,
            latency_ms_p95=payload_obj.latency_ms_p95,
            memory_mb_p95=payload_obj.memory_mb_p95,
            context_overlap=payload_obj.context_overlap,
            failure_rate=payload_obj.failure_rate,
            hardware_profile=payload_obj.hardware_profile,
            raw_measurements_count=payload_obj.raw_measurements_count,
            created_at=datetime.now(UTC),
        )
        self._session.add(run)
        await self._session.flush()
        return {
            "run_id": str(payload_obj.run_id),
            "created": True,
            "candidate_alias": payload_obj.candidate_alias,
            "candidate_digest": payload_obj.candidate_digest,
            "quality_score": payload_obj.quality_score,
        }

    async def propose_promotion(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(PromotionProposalPayload, payload)
        suite = await self._session.get(
            ModelBenchmarkSuite,
            {"owner_id": owner, "suite_id": payload_obj.suite_id},
        )
        if suite is None or suite.suite_version != payload_obj.suite_version:
            raise ModelGovernanceConflict("promotion suite no longer matches registered suite state")

        run = await self._session.get(ModelBenchmarkRun, {"owner_id": owner, "run_id": payload_obj.benchmark_run_id})
        if run is None:
            raise ModelGovernanceConflict("promotion benchmark run is missing")
        if run.suite_id != payload_obj.suite_id:
            raise ModelGovernanceConflict("promotion benchmark run is from a different suite")

        if run.sample_size < payload_obj.minimum_sample_size:
            raise ModelGovernanceConflict("promotion decision does not satisfy minimum sample size")

        state = await self._get_or_bootstrap_state(owner)
        if run.quality_score < state.promotion_quality_threshold:
            raise ModelGovernanceConflict("benchmark quality is below promotion threshold")

        incumbent_run = await self._get_latest_benchmark_run_for_candidate(
            owner_id=owner,
            suite_id=payload_obj.suite_id,
            suite_version=payload_obj.suite_version,
            candidate_alias=state.active_chat_alias or self._settings.model_evolution_active_chat_alias,
            candidate_digest=state.active_chat_digest
            if state.active_chat_digest is not None
            else self._settings.model_evolution_active_chat_digest,
        )
        await self._assert_model_regression_thresholds(
            suite=suite,
            incumbent_run=incumbent_run,
            candidate_run=run,
        )

        candidate = await self._session.get(
            ModelCandidate,
            {"owner_id": owner, "candidate_alias": payload_obj.candidate_alias},
        )
        if candidate is None or candidate.candidate_digest != payload_obj.candidate_digest:
            raise ModelGovernanceConflict("promotion candidate is not registered")

        proposal = await self._session.get(
            ModelPromotionProposal,
            {"owner_id": owner, "run_id": payload_obj.benchmark_run_id},
        )
        if proposal is not None:
            if (
                proposal.candidate_alias != payload_obj.candidate_alias
                or proposal.candidate_digest != payload_obj.candidate_digest
                or proposal.decision_digest != payload_obj.decision_digest
                or proposal.decision_reason != payload_obj.decision_reason
            ):
                raise ModelGovernanceConflict("promotion proposal already exists with different details")
            return {
                "candidate_alias": payload_obj.candidate_alias,
                "candidate_digest": payload_obj.candidate_digest,
                "proposal_run_id": str(payload_obj.benchmark_run_id),
                "created": False,
            }

        await self._ensure_state(owner)
        proposal = ModelPromotionProposal(
            owner_id=owner,
            suite_id=payload_obj.suite_id,
            suite_version=payload_obj.suite_version,
            benchmark_run_id=payload_obj.benchmark_run_id,
            candidate_alias=payload_obj.candidate_alias,
            candidate_digest=payload_obj.candidate_digest,
            incumbent_alias=payload_obj.incumbent_alias,
            incumbent_digest=payload_obj.incumbent_digest,
            decision_digest=payload_obj.decision_digest,
            minimum_sample_size=payload_obj.minimum_sample_size,
            decision_reason=payload_obj.decision_reason,
            created_at=datetime.now(UTC),
        )
        self._session.add(proposal)
        await self._session.flush()
        return {
            "created": True,
            "candidate_alias": payload_obj.candidate_alias,
            "candidate_digest": payload_obj.candidate_digest,
        }

    async def apply_activation(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(ModelActivationPayload, payload)
        state = await self._get_or_bootstrap_state(owner)

        if payload_obj.expected_previous_generation != state.active_generation:
            raise ModelGovernanceConflict("activation generation is stale")
        if payload_obj.generation <= state.active_generation:
            raise ModelGovernanceConflict("activation generation must be greater than active generation")

        candidate = await self._session.get(
            ModelCandidate,
            {"owner_id": owner, "candidate_alias": payload_obj.candidate_alias},
        )
        if candidate is None or candidate.candidate_digest != payload_obj.candidate_digest:
            raise ModelGovernanceConflict("activation candidate is not registered")
        if payload_obj.prior_generation_anchor != state.prior_generation_anchor:
            raise ModelGovernanceConflict("activation proposal is not based on current governance anchor")

        previous_alias = state.active_chat_alias
        previous_digest = state.active_chat_digest
        previous_generation = state.active_generation

        state.active_chat_alias = payload_obj.candidate_alias
        state.active_chat_digest = payload_obj.candidate_digest
        state.active_generation = payload_obj.generation
        state.prior_generation_anchor = payload_obj.prior_generation_anchor
        state.rollback_target_alias = payload_obj.rollback_target_alias
        state.rollback_target_digest = payload_obj.rollback_target_digest

        activation = ModelActivationHistory(
            history_id=uuid4(),
            owner_id=owner,
            action_scope=payload_obj.activation_scope,
            action_type="activation",
            from_alias=previous_alias,
            from_digest=previous_digest,
            to_alias=payload_obj.candidate_alias,
            to_digest=payload_obj.candidate_digest,
            applied_generation=payload_obj.generation,
            previous_generation=previous_generation,
            prior_generation_anchor=payload_obj.prior_generation_anchor,
            reason="applied",
            source_run_id=payload_obj.promotion_run_id,
            created_at=datetime.now(UTC),
        )
        self._session.add(activation)
        await self._session.flush()
        return {
            "applied": True,
            "from_alias": previous_alias,
            "from_digest": previous_digest,
            "to_alias": payload_obj.candidate_alias,
            "to_digest": payload_obj.candidate_digest,
            "generation": payload_obj.generation,
        }

    async def apply_rollback(
        self,
        *,
        owner_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        owner = _validate_owner_id(owner_id)
        if not self._settings.model_evolution_enabled:
            raise ModelGovernanceDisabled("model evolution is disabled")

        payload_obj = self._parse(ModelRollbackPayload, payload)
        state = await self._get_or_bootstrap_state(owner)

        if payload_obj.from_alias != state.active_chat_alias:
            raise ModelGovernanceConflict("rollback source alias does not match current active model")
        if payload_obj.from_digest != state.active_chat_digest:
            raise ModelGovernanceConflict("rollback source digest does not match current active model")

        candidate = await self._session.get(
            ModelCandidate,
            {"owner_id": owner, "candidate_alias": payload_obj.to_alias},
        )
        if candidate is None or candidate.candidate_digest != payload_obj.to_digest:
            raise ModelGovernanceConflict("rollback target is not a registered candidate")
        if payload_obj.rollback_generation <= 0:
            raise ModelGovernanceConflict("rollback generation must be positive")

        previous_alias = state.active_chat_alias
        previous_digest = state.active_chat_digest
        previous_generation = state.active_generation

        state.active_chat_alias = payload_obj.to_alias
        state.active_chat_digest = payload_obj.to_digest
        state.active_generation = payload_obj.rollback_generation

        activation = ModelActivationHistory(
            history_id=uuid4(),
            owner_id=owner,
            action_scope=payload_obj.activation_scope,
            action_type="rollback",
            from_alias=previous_alias,
            from_digest=previous_digest,
            to_alias=payload_obj.to_alias,
            to_digest=payload_obj.to_digest,
            applied_generation=payload_obj.rollback_generation,
            previous_generation=previous_generation,
            prior_generation_anchor=state.prior_generation_anchor,
            reason=payload_obj.reason,
            source_run_id=payload_obj.activation_run_id,
            created_at=datetime.now(UTC),
        )
        self._session.add(activation)
        await self._session.flush()
        return {
            "applied": True,
            "from_alias": previous_alias,
            "from_digest": previous_digest,
            "to_alias": payload_obj.to_alias,
            "to_digest": payload_obj.to_digest,
            "generation": payload_obj.rollback_generation,
        }

    async def _get_state(self, owner_id: str) -> ModelGovernanceState | None:
        return cast(
            ModelGovernanceState | None,
            await self._session.get(ModelGovernanceState, owner_id),
        )

    async def _ensure_state(self, owner_id: str) -> ModelGovernanceState:
        state = await self._get_state(owner_id)
        if state is not None:
            return state
        state = ModelGovernanceState(
            owner_id=owner_id,
            active_chat_alias=self._settings.model_evolution_active_chat_alias,
            active_chat_digest=self._settings.model_evolution_active_chat_digest,
            active_generation=self._settings.model_evolution_active_generation,
            prior_generation_anchor=self._settings.model_evolution_prior_generation_anchor,
            rollback_target_alias=self._settings.model_evolution_last_rollback_target_alias,
            rollback_target_digest=self._settings.model_evolution_last_rollback_target_digest,
            scoring_policy_version=self._settings.model_evolution_scoring_policy_version,
            benchmarking_policy_version=self._settings.model_evolution_benchmarking_policy_version,
            minimum_sample_size=self._settings.model_evolution_minimum_sample_size,
            promotion_quality_threshold=self._settings.model_evolution_promotion_quality_threshold,
            rollback_quality_threshold=self._settings.model_evolution_rollback_quality_threshold,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(state)
        await self._session.flush()
        return state

    async def _get_or_bootstrap_state(self, owner_id: str) -> ModelGovernanceState:
        state = await self._get_state(owner_id)
        if state is not None:
            return state
        state = await self._ensure_state(owner_id)
        state.active_chat_alias = self._settings.model_evolution_active_chat_alias
        if self._settings.model_evolution_active_chat_digest is not None:
            state.active_chat_digest = self._settings.model_evolution_active_chat_digest
        state.active_generation = max(1, self._settings.model_evolution_active_generation)
        state.prior_generation_anchor = self._settings.model_evolution_prior_generation_anchor
        state.scoring_policy_version = self._settings.model_evolution_scoring_policy_version
        state.benchmarking_policy_version = self._settings.model_evolution_benchmarking_policy_version
        state.minimum_sample_size = self._settings.model_evolution_minimum_sample_size
        state.promotion_quality_threshold = self._settings.model_evolution_promotion_quality_threshold
        state.rollback_quality_threshold = self._settings.model_evolution_rollback_quality_threshold
        state.rollback_target_alias = self._settings.model_evolution_last_rollback_target_alias
        state.rollback_target_digest = self._settings.model_evolution_last_rollback_target_digest
        await self._session.flush()
        return state

    async def _get_latest_benchmark_run_for_candidate(
        self,
        *,
        owner_id: str,
        suite_id: UUID,
        suite_version: str,
        candidate_alias: str | None,
        candidate_digest: str | None,
    ) -> ModelBenchmarkRun | None:
        if candidate_alias is None or candidate_digest is None:
            return None
        statement = (
            select(ModelBenchmarkRun)
            .where(
                and_(
                    ModelBenchmarkRun.owner_id == owner_id,
                    ModelBenchmarkRun.suite_id == suite_id,
                    ModelBenchmarkRun.suite_version == suite_version,
                    ModelBenchmarkRun.candidate_alias == candidate_alias,
                    ModelBenchmarkRun.candidate_digest == candidate_digest,
                )
            )
            .order_by(ModelBenchmarkRun.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def _assert_model_regression_thresholds(
        self,
        *,
        suite: ModelBenchmarkSuite,
        candidate_run: ModelBenchmarkRun,
        incumbent_run: ModelBenchmarkRun | None,
    ) -> None:
        if incumbent_run is None:
            return
        if not suite.target_metrics:
            return

        tolerance = suite.regression_tolerance
        for raw_metric in suite.target_metrics:
            metric = raw_metric.strip().lower()
            if metric == "quality":
                minimum_quality = incumbent_run.quality_score * (1.0 - tolerance)
                if candidate_run.quality_score < minimum_quality:
                    raise ModelGovernanceConflict(
                        "candidate quality regressed beyond tolerance"
                    )
                continue
            if metric == "latency_ms_p95":
                max_latency = incumbent_run.latency_ms_p95 * (1.0 + tolerance)
                if candidate_run.latency_ms_p95 > max_latency:
                    raise ModelGovernanceConflict(
                        "candidate latency regressed beyond tolerance"
                    )
                continue
            if metric == "memory_mb_p95":
                max_memory = incumbent_run.memory_mb_p95 * (1.0 + tolerance)
                if candidate_run.memory_mb_p95 > max_memory:
                    raise ModelGovernanceConflict(
                        "candidate memory regressed beyond tolerance"
                    )
                continue
            if metric == "context_overlap":
                minimum_context_overlap = incumbent_run.context_overlap * (1.0 - tolerance)
                if candidate_run.context_overlap < minimum_context_overlap:
                    raise ModelGovernanceConflict(
                        "candidate context overlap regressed beyond tolerance"
                    )
                continue
            if metric == "failure_rate":
                max_failure_rate = incumbent_run.failure_rate * (1.0 + tolerance)
                if candidate_run.failure_rate > max_failure_rate:
                    raise ModelGovernanceConflict(
                        "candidate failure rate regressed beyond tolerance"
                    )
                continue
            raise ModelGovernanceConflict(f"unsupported benchmark metric: {raw_metric}")

    def _parse[T](self, model: type[T], payload: dict[str, Any]) -> T:
        try:
            if not isinstance(payload, dict):
                raise TypeError("payload must be a dict")
            return model.model_validate(payload)
        except (TypeError, ValueError, ValidationError) as exc:
            raise ModelGovernanceConflict(f"invalid {model.__name__} payload") from exc


@dataclass(frozen=True, slots=True)
class ModelBenchmarkRunRecord:
    suite_id: UUID
    suite_version: str
    candidate_alias: str
    candidate_digest: str
    sample_size: int
    quality_score: float
    latency_ms_p95: float
    memory_mb_p95: float
    context_overlap: float
    failure_rate: float
    hardware_profile: str
    raw_measurements_count: int

    @staticmethod
    def from_orm(row: ModelBenchmarkRun) -> "ModelBenchmarkRunRecord":
        return ModelBenchmarkRunRecord(
            suite_id=row.suite_id,
            suite_version=row.suite_version,
            candidate_alias=row.candidate_alias,
            candidate_digest=row.candidate_digest,
            sample_size=row.sample_size,
            quality_score=row.quality_score,
            latency_ms_p95=row.latency_ms_p95,
            memory_mb_p95=row.memory_mb_p95,
            context_overlap=row.context_overlap,
            failure_rate=row.failure_rate,
            hardware_profile=row.hardware_profile,
            raw_measurements_count=row.raw_measurements_count,
        )

    def matches(self, payload: BenchmarkRunPayload) -> bool:
        return (
            self.suite_id == payload.suite_id
            and self.suite_version == payload.suite_version
            and self.candidate_alias == payload.candidate_alias
            and self.candidate_digest == payload.candidate_digest
            and self.sample_size == payload.sample_size
            and self.quality_score == payload.quality_score
            and self.latency_ms_p95 == payload.latency_ms_p95
            and self.memory_mb_p95 == payload.memory_mb_p95
            and self.context_overlap == payload.context_overlap
            and self.failure_rate == payload.failure_rate
            and self.hardware_profile == payload.hardware_profile
            and self.raw_measurements_count == payload.raw_measurements_count
        )


def _rows_match(row: ModelBenchmarkRunRecord, payload: BenchmarkRunPayload) -> bool:
    return row.matches(payload)


__all__ = [
    "ModelGovernanceConflict",
    "ModelGovernanceDisabled",
    "ModelGovernanceError",
    "ModelGovernanceService",
    "model_governance_dependency_payload",
]
