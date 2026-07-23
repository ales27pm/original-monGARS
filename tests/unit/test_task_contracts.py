from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mongars.adaptation.feedback import PreferenceFeedback
from mongars.adaptation.mimicry import propose_profile_delta
from mongars.orchestrator.personality import PersonalitySnapshot
from mongars.rm.contracts import (
    TASK_KIND_SCHEMA_VERSION,
    TASK_EXECUTOR_OPERATION_SCHEMA_VERSION,
    TASK_OPERATION_CONTRACTS,
    UnsupportedTaskKind,
    task_operation_contract,
    normalize_task_payload,
)


def _document_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "staging_id": str(uuid4()),
        "original_filename": "notes.txt",
        "source_sha256": "a" * 64,
        "detected_mime_type": "text/plain",
        "byte_size": 5,
        "source_timestamp": "2026-07-22T12:30:00Z",
        "received_at": "2026-07-22T12:31:00Z",
        "source_time_basis": "user_supplied",
    }
    payload.update(overrides)
    return payload


def test_unknown_task_kind_is_rejected() -> None:
    with pytest.raises(UnsupportedTaskKind, match="unsupported task kind"):
        normalize_task_payload("shell.execute", {"command": "id"})


def test_unknown_task_version_is_rejected() -> None:
    with pytest.raises(UnsupportedTaskKind, match="unsupported task kind"):
        normalize_task_payload(f"memory.search:{TASK_KIND_SCHEMA_VERSION}.x", {"query": "notes"})


def test_task_operations_are_predefined_and_versioned() -> None:
    assert TASK_KIND_SCHEMA_VERSION == "v1"
    assert TASK_EXECUTOR_OPERATION_SCHEMA_VERSION == "exec-v1"
    assert len(TASK_OPERATION_CONTRACTS) == 14

    for operation in TASK_OPERATION_CONTRACTS.values():
        assert operation.schema_version == TASK_KIND_SCHEMA_VERSION
        assert operation.policy_key
        assert operation.output_byte_limit >= 16_384
        assert operation.allow_network is False


def test_supported_task_kinds_are_explicitly_versioned() -> None:
    assert TASK_KIND_SCHEMA_VERSION == "v1"


def test_unknown_task_kind_has_no_contract() -> None:
    with pytest.raises(UnsupportedTaskKind, match="unsupported task kind"):
        task_operation_contract("shell.execute")


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("memory.search", {"query": "notes", "unexpected": True}),
        ("memory.note.create", {"text": "remember", "unexpected": True}),
        ("memory.reindex", {"unexpected": True}),
        ("document.ingest", _document_payload(unexpected=True)),
        ("model.candidate.register", {"candidate_alias": " new-candidate ", "unexpected": True}),
        ("model.benchmark.suite.create", {"unexpected": True}),
        ("model.benchmark.run", {"unexpected": True}),
        ("model.promotion.propose", {"unexpected": True}),
        ("model.activation.apply", {"unexpected": True}),
        ("model.rollback.apply", {"unexpected": True}),
        ("evolution.proposal.generate", {"proposals": ("op-a",), "unexpected": True}),
        ("evolution.proposal.execute", {"proposal_ids": ("op-a",), "unexpected": True}),
        ("execution.sandbox.echo", {"input_text": "x", "unexpected": True}),
    ],
)
def test_extra_payload_fields_are_rejected(
    kind: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        normalize_task_payload(kind, payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("memory.search", {}),
        ("memory.search", {"query": ""}),
        ("memory.search", {"query": " \t\n "}),
        ("memory.search", {"query": "notes", "top_k": 0}),
        ("memory.note.create", {}),
        ("memory.note.create", {"text": ""}),
        ("memory.note.create", {"text": "note", "sensitivity": "public"}),
        ("memory.note.create", {"text": "note", "retention_class": "forever"}),
        ("memory.reindex", {"document_id": "not-a-uuid"}),
        ("memory.reindex", {"batch_size": 0}),
        ("document.ingest", {}),
        ("document.ingest", _document_payload(staging_id="not-a-uuid")),
        ("document.ingest", _document_payload(source_sha256="A" * 64)),
        (
            "document.ingest",
            _document_payload(detected_mime_type="application/octet-stream"),
        ),
        ("document.ingest", _document_payload(byte_size=0)),
        (
            "document.ingest",
            _document_payload(source_timestamp="2026-07-22T12:30:00"),
        ),
        (
            "document.ingest",
            _document_payload(received_at="2026-07-22T12:31:00"),
        ),
        ("document.ingest", _document_payload(source_time_basis="filesystem_mtime")),
        ("document.ingest", _document_payload(sensitivity="public")),
        ("document.ingest", _document_payload(retention_class="forever")),
        (
            "personality.profile.apply",
            {
                "feedback_id": "not-a-uuid",
                "feedback_digest": "a" * 64,
                "expected_revision": 0,
                "expected_profile_digest": "a" * 64,
                "changed_dimension": "directness",
                "conflict": False,
                "previous": None,
                "proposed": {
                    "confidence": 0.9,
                    "dimension": "directness",
                    "evidence_count": 1,
                    "value": 0.8,
                },
                "target_preferences": [
                    {
                        "confidence": 0.9,
                        "dimension": "directness",
                        "evidence_count": 1,
                        "value": 0.8,
                    }
                ],
                "target_profile_digest": "a" * 64,
                "target_revision": 1,
            },
        ),
        (
            "model.candidate.register",
            {
                "candidate_alias": " ",
                "candidate_digest": "a" * 64,
                "scoring_policy_version": "v1",
                "requested_by": "governance",
            },
        ),
        (
            "model.benchmark.suite.create",
            {
                "suite_id": "not-a-uuid",
                "suite_version": "v1",
                "scoring_policy_version": "v1",
                "target_metrics": ["quality", "latency"],
                "minimum_sample_size": 0,
                "regression_tolerance": 0.1,
            },
        ),
        (
            "model.benchmark.run",
            {
                "run_id": str(uuid4()),
                "suite_id": str(uuid4()),
                "candidate_alias": "model-candidate",
                "candidate_digest": "g" * 64,
                "sample_size": 0,
                "quality_score": 0.75,
                "latency_ms_p95": 1000.0,
                "memory_mb_p95": 32.0,
                "context_overlap": 0.2,
                "failure_rate": 0.1,
                "hardware_profile": "a100",
                "raw_measurements_count": 10,
            },
        ),
        (
            "model.promotion.propose",
            {
                "candidate_alias": "model-candidate",
                "candidate_digest": "a" * 64,
                "incumbent_alias": "old-model",
                "incumbent_digest": "a" * 63 + "g",
                "benchmark_run_id": str(uuid4()),
                "suite_id": str(uuid4()),
                "suite_version": "v1",
                "decision_digest": "a" * 64,
                "minimum_sample_size": 10,
                "decision_reason": "improves quality",
            },
        ),
        (
            "model.activation.apply",
            {
                "candidate_alias": "model-candidate",
                "candidate_digest": "a" * 64,
                "rollback_target_alias": "old-model",
                "rollback_target_digest": "a" * 63 + "b",
                "generation": 0,
                "promotion_run_id": str(uuid4()),
                "expected_previous_generation": 0,
                "prior_generation_anchor": "abc",
            },
        ),
        (
            "model.rollback.apply",
            {
                "from_alias": "old-model",
                "from_digest": "a" * 64,
                "to_alias": "new-model",
                "to_digest": "a" * 64,
                "reason": "",
                "rollback_generation": 1,
                "activation_run_id": str(uuid4()),
            },
        ),
        ("evolution.proposal.generate", {}),
        ("evolution.proposal.generate", {"proposals": []}),
        ("evolution.proposal.execute", {}),
        ("evolution.proposal.execute", {"proposal_ids": []}),
        ("execution.sandbox.echo", {}),
        (
            "execution.sandbox.echo",
            {"input_text": "abc", "operation": "rotate"},
        ),
    ],
)
def test_invalid_payload_values_are_rejected(
    kind: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        normalize_task_payload(kind, payload)


def test_search_payload_is_normalized_with_defaults() -> None:
    source = {"query": "notes"}

    normalized = normalize_task_payload("memory.search", source)

    assert normalized == {"query": "notes", "top_k": 8}
    assert source == {"query": "notes"}


def test_note_payload_is_normalized_with_security_defaults() -> None:
    normalized = normalize_task_payload(
        "memory.note.create",
        {"text": "remember", "title": "Reminder"},
    )

    assert normalized == {
        "text": "remember",
        "title": "Reminder",
        "sensitivity": "private",
        "retention_class": "keep",
    }


def test_reindex_payload_is_normalized_with_bounded_defaults() -> None:
    assert normalize_task_payload("memory.reindex", {}) == {
        "document_id": None,
        "batch_size": 32,
    }


def test_document_payload_is_normalized_with_governance_defaults() -> None:
    staging_id = uuid4()
    source_timestamp = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    received_at = datetime(2026, 7, 22, 12, 31, tzinfo=UTC)
    source = {
        "staging_id": str(staging_id),
        "original_filename": "notes.txt",
        "source_sha256": "a" * 64,
        "detected_mime_type": "text/plain",
        "byte_size": 5,
        "source_timestamp": source_timestamp.isoformat(),
        "received_at": received_at.isoformat(),
        "source_time_basis": "user_supplied",
        "title": "Reviewed upload",
    }

    normalized = normalize_task_payload("document.ingest", source)

    assert normalized == {
        "staging_id": str(staging_id),
        "original_filename": "notes.txt",
        "source_sha256": "a" * 64,
        "detected_mime_type": "text/plain",
        "byte_size": 5,
        "source_timestamp": "2026-07-22T08:30:00Z",
        "received_at": "2026-07-22T12:31:00Z",
        "source_time_basis": "user_supplied",
        "title": "Reviewed upload",
        "sensitivity": "private",
        "retention_class": "keep",
    }
    assert source["source_timestamp"] == source_timestamp.isoformat()
    assert source["received_at"] == received_at.isoformat()


def test_personality_apply_payload_is_reconstructed_from_profile_delta() -> None:
    proposal = propose_profile_delta(
        PersonalitySnapshot.default(),
        PreferenceFeedback(
            feedback_id=uuid4(),
            dimension="directness",
            desired_value=0.8,
        ),
    )
    assert proposal is not None
    payload = proposal.as_task_payload()
    normalized = normalize_task_payload("personality.profile.apply", payload)
    assert normalized == payload


def test_model_governance_payloads_are_normalized_and_trusted() -> None:
    run_id = uuid4()
    promotion_run_id = uuid4()
    suite_id = uuid4()
    benchmark_run_id = uuid4()

    assert normalize_task_payload(
        "model.candidate.register",
        {
            "candidate_alias": " qwen3-cand ",
            "candidate_digest": "a" * 64,
            "scoring_policy_version": "bench-v2",
            "requested_by": "governance-agent",
        },
    ) == {
        "candidate_alias": "qwen3-cand",
        "candidate_digest": "a" * 64,
        "scoring_policy_version": "bench-v2",
        "requested_by": "governance-agent",
    }
    assert normalize_task_payload(
        "model.benchmark.suite.create",
        {
            "suite_id": str(suite_id),
            "suite_version": "suite-v1",
            "scoring_policy_version": "bench-v2",
            "target_metrics": ["quality", "latency"],
            "minimum_sample_size": 42,
            "regression_tolerance": 0.04,
        },
    ) == {
        "suite_id": str(suite_id),
        "suite_version": "suite-v1",
        "scoring_policy_version": "bench-v2",
        "target_metrics": ("quality", "latency"),
        "minimum_sample_size": 42,
        "regression_tolerance": 0.04,
    }
    assert normalize_task_payload(
        "model.benchmark.run",
        {
            "run_id": str(run_id),
            "suite_id": str(suite_id),
            "candidate_alias": "run-cand",
            "candidate_digest": "a" * 64,
            "sample_size": 100,
            "quality_score": 0.89,
            "latency_ms_p95": 1234.5,
            "memory_mb_p95": 42.0,
            "context_overlap": 0.33,
            "failure_rate": 0.02,
            "hardware_profile": "a100",
            "raw_measurements_count": 150,
        },
    ) == {
        "run_id": str(run_id),
        "suite_id": str(suite_id),
        "candidate_alias": "run-cand",
        "candidate_digest": "a" * 64,
        "sample_size": 100,
        "quality_score": 0.89,
        "latency_ms_p95": 1234.5,
        "memory_mb_p95": 42.0,
        "context_overlap": 0.33,
        "failure_rate": 0.02,
        "hardware_profile": "a100",
        "raw_measurements_count": 150,
    }
    assert normalize_task_payload(
        "model.promotion.propose",
        {
            "candidate_alias": "run-cand",
            "candidate_digest": "a" * 64,
            "incumbent_alias": "old-model",
            "incumbent_digest": "b" * 64,
            "benchmark_run_id": str(benchmark_run_id),
            "suite_id": str(suite_id),
            "suite_version": "suite-v1",
            "decision_digest": "c" * 64,
            "minimum_sample_size": 64,
            "decision_reason": "candidate improved quality and latency",
        },
    ) == {
        "candidate_alias": "run-cand",
        "candidate_digest": "a" * 64,
        "incumbent_alias": "old-model",
        "incumbent_digest": "b" * 64,
        "benchmark_run_id": str(benchmark_run_id),
        "suite_id": str(suite_id),
        "suite_version": "suite-v1",
        "decision_digest": "c" * 64,
        "minimum_sample_size": 64,
        "decision_reason": "candidate improved quality and latency",
    }
    assert normalize_task_payload(
        "model.activation.apply",
        {
            "candidate_alias": "run-cand",
            "candidate_digest": "a" * 64,
            "rollback_target_alias": "old-model",
            "rollback_target_digest": "b" * 64,
            "generation": 2,
            "promotion_run_id": str(run_id),
            "expected_previous_generation": 1,
            "prior_generation_anchor": "stable-2026",
            "activation_scope": "chat_model",
        },
    ) == {
        "candidate_alias": "run-cand",
        "candidate_digest": "a" * 64,
        "rollback_target_alias": "old-model",
        "rollback_target_digest": "b" * 64,
        "generation": 2,
        "promotion_run_id": str(run_id),
        "expected_previous_generation": 1,
        "prior_generation_anchor": "stable-2026",
        "activation_scope": "chat_model",
    }
    assert normalize_task_payload(
        "model.rollback.apply",
        {
            "from_alias": "run-cand",
            "from_digest": "a" * 64,
            "to_alias": "old-model",
            "to_digest": "b" * 64,
            "reason": "quality regression in benchmark",
            "rollback_generation": 2,
            "activation_run_id": str(promotion_run_id),
            "activation_scope": "chat_model",
        },
    ) == {
        "from_alias": "run-cand",
        "from_digest": "a" * 64,
        "to_alias": "old-model",
        "to_digest": "b" * 64,
        "reason": "quality regression in benchmark",
        "rollback_generation": 2,
        "activation_run_id": str(promotion_run_id),
        "activation_scope": "chat_model",
    }
    assert normalize_task_payload(
        "evolution.proposal.generate",
        {"proposals": ("op-a", "op-b", "op-a")},
    ) == {
        "proposals": ("op-a", "op-b", "op-a"),
    }
    assert normalize_task_payload(
        "evolution.proposal.execute",
        {
            "proposal_ids": ("op-a", "op-b"),
            "operator_note": "reviewer",
        },
    ) == {
        "proposal_ids": ("op-a", "op-b"),
        "operator_note": "reviewer",
    }
    assert normalize_task_payload(
        "execution.sandbox.echo",
        {"input_text": "abc", "operation": "reverse"},
    ) == {
        "input_text": "abc",
        "operation": "reverse",
    }
