from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from mongars.adaptation.mimicry import ProfileDeltaProposal
from mongars.orchestrator._cognitive_validation import validate_sha256_digest
from mongars.orchestrator.personality import PersonalityDimension, PersonalityPreference, PersonalitySnapshot

TaskKind: TypeAlias = Literal[
    "memory.search",
    "memory.note.create",
    "memory.reindex",
    "document.ingest",
    "personality.profile.apply",
    "evolution.proposal.generate",
    "evolution.proposal.execute",
    "execution.sandbox.echo",
    "model.candidate.register",
    "model.benchmark.suite.create",
    "model.benchmark.run",
    "model.promotion.propose",
    "model.activation.apply",
    "model.rollback.apply",
]
TaskPolicyKey: TypeAlias = tuple[str, str]

TASK_KIND_SCHEMA_VERSION: str = "v1"
TASK_EXECUTOR_OPERATION_SCHEMA_VERSION: str = "exec-v1"

SUPPORTED_TASK_KINDS: tuple[TaskKind, ...] = (
    "memory.search",
    "memory.note.create",
    "memory.reindex",
    "document.ingest",
    "personality.profile.apply",
    "evolution.proposal.generate",
    "evolution.proposal.execute",
    "execution.sandbox.echo",
    "model.candidate.register",
    "model.benchmark.suite.create",
    "model.benchmark.run",
    "model.promotion.propose",
    "model.activation.apply",
    "model.rollback.apply",
)


@dataclass(frozen=True, slots=True)
class TaskOperationContract:
    """Closed schema for one predefined and versioned execution operation."""

    operation_id: str
    kind: TaskKind
    policy_key: TaskPolicyKey
    schema_version: str
    requires_approval: bool
    output_byte_limit: int
    allow_network: bool


TASK_OPERATION_CONTRACTS: dict[TaskKind, TaskOperationContract] = {
    "memory.search": TaskOperationContract(
        operation_id="mains-virtuelles/memory.search@v1",
        kind="memory.search",
        policy_key=("memory", "search"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=False,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "memory.note.create": TaskOperationContract(
        operation_id="mains-virtuelles/memory.note.create@v1",
        kind="memory.note.create",
        policy_key=("memory", "note.create"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "memory.reindex": TaskOperationContract(
        operation_id="mains-virtuelles/memory.reindex@v1",
        kind="memory.reindex",
        policy_key=("memory", "reindex"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "document.ingest": TaskOperationContract(
        operation_id="mains-virtuelles/document.ingest@v1",
        kind="document.ingest",
        policy_key=("document", "ingest"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "personality.profile.apply": TaskOperationContract(
    operation_id="mains-virtuelles/personality.profile.apply@v1",
    kind="personality.profile.apply",
    policy_key=("personality", "profile.apply"),
    schema_version=TASK_KIND_SCHEMA_VERSION,
    requires_approval=True,
    output_byte_limit=16_384,
    allow_network=False,
    ),
    "evolution.proposal.generate": TaskOperationContract(
        operation_id="mains-virtuelles/evolution.proposal.generate@v1",
        kind="evolution.proposal.generate",
        policy_key=("evolution", "proposal.generate"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=False,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "evolution.proposal.execute": TaskOperationContract(
        operation_id="mains-virtuelles/evolution.proposal.execute@v1",
        kind="evolution.proposal.execute",
        policy_key=("evolution", "proposal.execute"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "execution.sandbox.echo": TaskOperationContract(
        operation_id="mains-virtuelles/execution.sandbox.echo@v1",
        kind="execution.sandbox.echo",
        policy_key=("execution", "sandbox.echo"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.candidate.register": TaskOperationContract(
        operation_id="mains-virtuelles/model.candidate.register@v1",
        kind="model.candidate.register",
        policy_key=("model", "candidate.register"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.benchmark.suite.create": TaskOperationContract(
        operation_id="mains-virtuelles/model.benchmark.suite.create@v1",
        kind="model.benchmark.suite.create",
        policy_key=("model", "benchmark.suite.create"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.benchmark.run": TaskOperationContract(
        operation_id="mains-virtuelles/model.benchmark.run@v1",
        kind="model.benchmark.run",
        policy_key=("model", "benchmark.run"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.promotion.propose": TaskOperationContract(
        operation_id="mains-virtuelles/model.promotion.propose@v1",
        kind="model.promotion.propose",
        policy_key=("model", "promotion.propose"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.activation.apply": TaskOperationContract(
        operation_id="mains-virtuelles/model.activation.apply@v1",
        kind="model.activation.apply",
        policy_key=("model", "activation.apply"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
    "model.rollback.apply": TaskOperationContract(
        operation_id="mains-virtuelles/model.rollback.apply@v1",
        kind="model.rollback.apply",
        policy_key=("model", "rollback.apply"),
        schema_version=TASK_KIND_SCHEMA_VERSION,
        requires_approval=True,
        output_byte_limit=16_384,
        allow_network=False,
    ),
}

TASK_POLICY_KEYS: dict[TaskKind, TaskPolicyKey] = {
    kind: contract.policy_key for kind, contract in TASK_OPERATION_CONTRACTS.items()
}


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MemorySearchPayload(StrictPayload):
    query: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=8, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query must contain non-whitespace text")
        return value


class MemoryNoteCreatePayload(StrictPayload):
    text: str = Field(min_length=1, max_length=2_000_000)
    title: str | None = Field(default=None, max_length=500)
    sensitivity: str = Field(default="private", pattern="^(private|shared|restricted)$")
    retention_class: str = Field(default="keep", pattern="^(keep|ttl_30d|ttl_90d|legal_hold)$")


class MemoryReindexPayload(StrictPayload):
    document_id: UUID | None = None
    batch_size: int = Field(default=32, ge=1, le=128)


class DocumentIngestPayload(StrictPayload):
    staging_id: UUID
    original_filename: str = Field(min_length=1, max_length=255)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    detected_mime_type: Literal[
        "text/plain",
        "text/markdown",
        "text/html",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    byte_size: int = Field(ge=1, le=20_000_000)
    source_timestamp: datetime
    received_at: datetime
    source_time_basis: Literal["user_supplied"]
    title: str | None = Field(default=None, max_length=500)
    sensitivity: str = Field(default="private", pattern="^(private|shared|restricted)$")
    retention_class: str = Field(default="keep", pattern="^(keep|ttl_30d|ttl_90d|legal_hold)$")

    @field_validator("source_timestamp")
    @classmethod
    def normalize_source_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_timestamp must include a timezone")
        return value.astimezone(UTC)

    @field_validator("received_at")
    @classmethod
    def normalize_received_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("received_at must include a timezone")
        return value.astimezone(UTC)


class _ProfilePreferencePayload(StrictPayload):
    dimension: PersonalityDimension
    value: float
    confidence: float
    evidence_count: int


class PersonalityProfileApplyPayload(StrictPayload):
    changed_dimension: PersonalityDimension
    conflict: bool
    expected_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=0, le=2_147_483_647)
    feedback_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    feedback_id: UUID
    previous: _ProfilePreferencePayload | None
    proposed: _ProfilePreferencePayload
    target_preferences: tuple[_ProfilePreferencePayload, ...] = Field(min_length=1, max_length=5)
    target_profile_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_revision: int = Field(ge=1, le=2_147_483_647)


class ModelCandidateRegisterPayload(StrictPayload):
    candidate_alias: str = Field(min_length=1, max_length=255)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_policy_version: str = Field(min_length=1, max_length=32)
    requested_by: str = Field(min_length=1, max_length=128)

    @field_validator("candidate_alias")
    @classmethod
    def trim_candidate_alias(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate_alias must be a non-empty trimmed string")
        return normalized


class BenchmarkSuiteCreatePayload(StrictPayload):
    suite_id: UUID
    suite_version: str = Field(min_length=1, max_length=32)
    scoring_policy_version: str = Field(min_length=1, max_length=32)
    target_metrics: tuple[str, ...] = Field(min_length=1)
    minimum_sample_size: int = Field(ge=1, le=1_000_000)
    regression_tolerance: float = Field(ge=0.0, le=1.0)


class BenchmarkRunPayload(StrictPayload):
    run_id: UUID
    suite_id: UUID
    candidate_alias: str = Field(min_length=1, max_length=255)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_size: int = Field(ge=1, le=1_000_000)
    quality_score: float = Field(ge=0.0, le=1.0)
    latency_ms_p95: float = Field(gt=0.0, le=120_000.0)
    memory_mb_p95: float = Field(gt=0.0, le=1_000_000.0)
    context_overlap: float = Field(ge=0.0, le=1.0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    hardware_profile: str = Field(min_length=1, max_length=128)
    raw_measurements_count: int = Field(ge=0, le=10_000)

    @field_validator("candidate_alias")
    @classmethod
    def trim_candidate_alias(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("candidate_alias must be a non-empty trimmed string")
        return normalized


class PromotionProposalPayload(StrictPayload):
    candidate_alias: str = Field(min_length=1, max_length=255)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    incumbent_alias: str | None = None
    incumbent_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    benchmark_run_id: UUID
    suite_id: UUID
    suite_version: str = Field(min_length=1, max_length=32)
    decision_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_sample_size: int = Field(ge=1, le=1_000_000)
    decision_reason: str = Field(min_length=1, max_length=1_000)


class ModelActivationPayload(StrictPayload):
    candidate_alias: str = Field(min_length=1, max_length=255)
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rollback_target_alias: str | None = None
    rollback_target_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    generation: int = Field(ge=1, le=2_147_483_647)
    promotion_run_id: UUID
    expected_previous_generation: int = Field(ge=0, le=2_147_483_647)
    prior_generation_anchor: str = Field(min_length=1, max_length=128)
    activation_scope: Literal["chat_model"] = "chat_model"

    @field_validator("candidate_alias", "rollback_target_alias")
    @classmethod
    def trim_aliases(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("model alias must be a non-empty trimmed string when present")
        return normalized


class EvolutionProposalGeneratePayload(StrictPayload):
    proposals: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("proposals")
    @classmethod
    def normalize_and_validate_proposals(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if len(normalized) != len(value):
            # Keep deterministic order while rejecting accidental whitespace-only entries.
            if any(not item for item in normalized):
                raise ValueError("proposal ids must be non-empty strings")
        if any(not item for item in normalized):
            raise ValueError("proposal ids must be non-empty strings")
        return normalized


class EvolutionProposalExecutePayload(StrictPayload):
    proposal_ids: tuple[str, ...] = Field(min_length=1, max_length=20)
    operator_note: str | None = Field(default=None, max_length=1_000)

    @field_validator("proposal_ids")
    @classmethod
    def normalize_and_validate_execute_payload(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("proposal ids must be non-empty strings")
        return normalized


class ExecutionSandboxEchoPayload(StrictPayload):
    input_text: str = Field(min_length=1, max_length=32_000)
    operation: Literal["sha256", "reverse"] = "sha256"


class ModelRollbackPayload(StrictPayload):
    from_alias: str = Field(min_length=1, max_length=255)
    from_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    to_alias: str = Field(min_length=1, max_length=255)
    to_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=1_000)
    rollback_generation: int = Field(ge=1, le=2_147_483_647)
    activation_run_id: UUID
    activation_scope: Literal["chat_model"] = "chat_model"

    @field_validator("from_alias", "to_alias")
    @classmethod
    def trim_aliases(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model alias must be a non-empty trimmed string")
        return normalized


def task_operation_contract(kind: str) -> TaskOperationContract:
    contract = TASK_OPERATION_CONTRACTS.get(kind)  # type: ignore[arg-type]
    if contract is None:
        raise UnsupportedTaskKind(f"unsupported task kind: {kind}")
    return contract


def normalize_profile_apply_payload(payload: dict[str, Any]) -> ProfileDeltaProposal:
    validated = PersonalityProfileApplyPayload.model_validate(payload)
    validate_sha256_digest(
        [
            validated.expected_profile_digest,
            validated.feedback_digest,
            validated.target_profile_digest,
        ]
    )
    current = ProfileDeltaProposal(
        feedback_id=validated.feedback_id,
        feedback_digest=validated.feedback_digest,
        expected_revision=validated.expected_revision,
        expected_profile_digest=validated.expected_profile_digest,
        target_snapshot=PersonalitySnapshot(
            revision=validated.target_revision,
            source="explicit_feedback",
            profile_digest=validated.target_profile_digest,
            preferences=tuple(
                PersonalityPreference(
                    dimension=item.dimension,
                    value=item.value,
                    confidence=item.confidence,
                    evidence_count=item.evidence_count,
                )
                for item in validated.target_preferences
            ),
        ),
        changed_dimension=validated.changed_dimension,
        previous=_preference_payload_to_model(validated.previous),
        proposed=_preference_payload_to_model(validated.proposed),
        conflict=validated.conflict,
    )
    return current


def _preference_payload_to_model(
    preference: _ProfilePreferencePayload | None,
) -> PersonalityPreference | None:
    if preference is None:
        return None
    return PersonalityPreference(
        dimension=preference.dimension,
        value=preference.value,
        confidence=preference.confidence,
        evidence_count=preference.evidence_count,
    )


_PAYLOAD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "memory.search": TypeAdapter(MemorySearchPayload),
    "memory.note.create": TypeAdapter(MemoryNoteCreatePayload),
    "memory.reindex": TypeAdapter(MemoryReindexPayload),
    "document.ingest": TypeAdapter(DocumentIngestPayload),
    "personality.profile.apply": TypeAdapter(PersonalityProfileApplyPayload),
    "evolution.proposal.generate": TypeAdapter(EvolutionProposalGeneratePayload),
    "evolution.proposal.execute": TypeAdapter(EvolutionProposalExecutePayload),
    "execution.sandbox.echo": TypeAdapter(ExecutionSandboxEchoPayload),
    "model.candidate.register": TypeAdapter(ModelCandidateRegisterPayload),
    "model.benchmark.suite.create": TypeAdapter(BenchmarkSuiteCreatePayload),
    "model.benchmark.run": TypeAdapter(BenchmarkRunPayload),
    "model.promotion.propose": TypeAdapter(PromotionProposalPayload),
    "model.activation.apply": TypeAdapter(ModelActivationPayload),
    "model.rollback.apply": TypeAdapter(ModelRollbackPayload),
}


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "memory.search": MemorySearchPayload,
    "memory.note.create": MemoryNoteCreatePayload,
    "memory.reindex": MemoryReindexPayload,
    "document.ingest": DocumentIngestPayload,
    "personality.profile.apply": PersonalityProfileApplyPayload,
    "evolution.proposal.generate": EvolutionProposalGeneratePayload,
    "evolution.proposal.execute": EvolutionProposalExecutePayload,
    "execution.sandbox.echo": ExecutionSandboxEchoPayload,
    "model.candidate.register": ModelCandidateRegisterPayload,
    "model.benchmark.suite.create": BenchmarkSuiteCreatePayload,
    "model.benchmark.run": BenchmarkRunPayload,
    "model.promotion.propose": PromotionProposalPayload,
    "model.activation.apply": ModelActivationPayload,
    "model.rollback.apply": ModelRollbackPayload,
}


def normalize_task_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    _ = task_operation_contract(kind)
    adapter = _PAYLOAD_ADAPTERS.get(kind)
    model = _PAYLOAD_MODELS[kind]
    if adapter is None:
        raise UnsupportedTaskKind(f"unsupported task kind schema for {kind}")

    required_fields = {
        name for name, field in model.model_fields.items() if field.is_required()
    }
    unknown_fields = set(payload) - set(model.model_fields)
    try:
        validated = adapter.validate_python(payload)
    except ValidationError as exc:
        if unknown_fields and required_fields and not required_fields <= set(payload):
            unknown = ",".join(sorted(unknown_fields))
            raise ValidationError.from_exception_data(
                "normalize_task_payload",
                [
                    {
                        "type": "extra_forbidden",
                        "loc": ("payload",),
                        "msg": f"extra fields are not permitted: {unknown}",
                        "input": payload,
                    }
                ],
            ) from exc
        raise
    if not isinstance(validated, BaseModel):
        raise TypeError("task payload adapter returned an invalid value")
    payload_data = validated.model_dump(mode="json")
    if kind == "model.benchmark.suite.create":
        payload_data["target_metrics"] = tuple(payload_data["target_metrics"])
    return payload_data


class UnsupportedTaskKind(ValueError):
    pass


def supported_kind_count() -> int:
    return len(SUPPORTED_TASK_KINDS)


__all__ = [
    "TASK_EXECUTOR_OPERATION_SCHEMA_VERSION",
    "TASK_KIND_SCHEMA_VERSION",
    "SUPPORTED_TASK_KINDS",
    "TASK_OPERATION_CONTRACTS",
    "TASK_POLICY_KEYS",
    "TaskKind",
    "TaskOperationContract",
    "TaskPolicyKey",
    "task_operation_contract",
    "DocumentIngestPayload",
    "PersonalityProfileApplyPayload",
    "MemoryNoteCreatePayload",
    "MemoryReindexPayload",
    "MemorySearchPayload",
    "ModelCandidateRegisterPayload",
    "BenchmarkSuiteCreatePayload",
    "BenchmarkRunPayload",
    "EvolutionProposalGeneratePayload",
    "EvolutionProposalExecutePayload",
    "ExecutionSandboxEchoPayload",
    "PromotionProposalPayload",
    "ModelActivationPayload",
    "ModelRollbackPayload",
    "UnsupportedTaskKind",
    "normalize_profile_apply_payload",
    "ValidationError",
    "normalize_task_payload",
]
