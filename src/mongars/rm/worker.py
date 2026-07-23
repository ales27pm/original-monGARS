from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from mongars.config import Settings, get_settings
from mongars.db.models import DocumentStaging
from mongars.db.session import Database
from mongars.embeddings.errors import EmbeddingError
from mongars.embeddings.ollama import OllamaEmbeddingProvider
from mongars.embeddings.service import EmbeddingService
from mongars.evolution.governance import ModelGovernanceError, ModelGovernanceService
from mongars.events.repository import EventRepository
from mongars.inference.base import InferenceBackend
from mongars.inference.ollama import OllamaBackend
from mongars.ingestion.errors import IngestionError
from mongars.ingestion.isolation import DocumentParser
from mongars.ingestion.models import (
    DocumentMediaType,
    DocumentProvenance,
    IngestionContext,
    ValidatedUpload,
)
from mongars.ingestion.runtime import document_parser_from_settings
from mongars.ingestion.staging import DocumentStagingRepository
from mongars.logging import configure_logging
from mongars.adaptation.repository import PersonalityProfileConflict, PersonalityProfileDataError
from mongars.adaptation.repository import PersonalityRepository
from mongars.rm.contracts import normalize_profile_apply_payload
from mongars.memory.repository import MemoryGovernanceConflict, MemoryRepository
from mongars.memory.service import IngestResult, MemoryService
from mongars.rm.repository import TaskRepository
from mongars.rm.runtime_heartbeat import WorkerRuntimeHeartbeat
from mongars.rm.service import TaskIntegrityError, TaskService
from mongars.security.policy import ActionClassification, ToolPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutionClaim:
    task_id: UUID
    execution_token: UUID
    owner_id: str
    kind: str
    trace_id: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    result: dict[str, Any]
    finalized: bool = False


class TaskLeaseLost(RuntimeError):
    pass


class Worker:
    _EXECUTOR_SECURITY_REVIEW_REQUIRED_KINDS: frozenset[str] = frozenset(
        {
            "evolution.proposal.generate",
            "evolution.proposal.execute",
            "execution.sandbox.echo",
        }
    )

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        inference: InferenceBackend,
        embeddings: EmbeddingService,
        document_parser: DocumentParser | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._inference = inference
        self._embeddings = embeddings
        self._document_parser = document_parser or document_parser_from_settings(settings)
        self._runtime_heartbeat = WorkerRuntimeHeartbeat(
            settings=settings,
            database=database,
            embeddings=embeddings,
            document_parser=self._document_parser,
        )
        self._next_retention_sweep = 0.0
        self._policy = ToolPolicy(
            {
                ("memory", "search"): ActionClassification.READ_ONLY,
                ("memory", "note.create"): ActionClassification.LOCAL_MUTATION,
                ("memory", "reindex"): ActionClassification.LOCAL_MUTATION,
                ("document", "ingest"): ActionClassification.LOCAL_MUTATION,
                ("personality", "profile.apply"): ActionClassification.LOCAL_MUTATION,
                ("model", "candidate.register"): ActionClassification.LOCAL_MUTATION,
                ("model", "benchmark.suite.create"): ActionClassification.LOCAL_MUTATION,
                ("model", "benchmark.run"): ActionClassification.LOCAL_MUTATION,
                ("model", "promotion.propose"): ActionClassification.LOCAL_MUTATION,
                ("model", "activation.apply"): ActionClassification.LOCAL_MUTATION,
                ("model", "rollback.apply"): ActionClassification.LOCAL_MUTATION,
                ("evolution", "proposal.generate"): ActionClassification.READ_ONLY,
                ("evolution", "proposal.execute"): ActionClassification.LOCAL_MUTATION,
                ("execution", "sandbox.echo"): ActionClassification.LOCAL_MUTATION,
            }
        )

    async def run_once(self) -> bool:
        async with self._database.session_factory() as session, session.begin():
            repository = TaskRepository(session)
            if monotonic() >= self._next_retention_sweep:
                expired = await MemoryRepository(session).expire_due(
                    owner_id=self._settings.owner_id
                )
                self._next_retention_sweep = monotonic() + self._settings.retention_sweep_seconds
                if expired:
                    await EventRepository(session).record(
                        owner_id=self._settings.owner_id,
                        trace_id="retention_sweep",
                        actor="worker",
                        event_type="memory_expired",
                        summary=f"Expired {expired} retained memory documents",
                        payload={"document_count": expired},
                    )
                expired_uploads = await DocumentStagingRepository(session).cleanup_stale(
                    owner_id=self._settings.owner_id
                )
                for upload in expired_uploads:
                    await EventRepository(session).record(
                        owner_id=upload.owner_id,
                        trace_id=upload.trace_id,
                        actor="worker",
                        event_type="document_ingest_failed",
                        summary="Document ingestion approval expired",
                        payload={
                            "task_id": str(upload.task_id),
                            "error_code": "approval_expired",
                        },
                    )
            recoveries = await repository.recover_expired_leases()
            events = EventRepository(session)
            for recovery in recoveries:
                failed = recovery.status == "failed"
                if failed and recovery.kind == "document.ingest":
                    await DocumentStagingRepository(session).delete_for_task(
                        owner_id=recovery.owner_id,
                        task_id=recovery.task_id,
                    )
                    await events.record(
                        owner_id=recovery.owner_id,
                        trace_id=recovery.trace_id,
                        actor="worker",
                        event_type="document_ingest_failed",
                        summary="Document ingestion failed after its final lease expired",
                        payload={
                            "task_id": str(recovery.task_id),
                            "error_code": "lease_exhausted",
                        },
                    )
                await events.record(
                    owner_id=recovery.owner_id,
                    trace_id=recovery.trace_id,
                    actor="worker",
                    event_type="task_failed" if failed else "task_requeued",
                    summary=(
                        f"Failed {recovery.kind} after its final lease expired"
                        if failed
                        else f"Requeued {recovery.kind} after its lease expired"
                    ),
                    payload={
                        "task_id": str(recovery.task_id),
                        "reason": recovery.error_text,
                    },
                )
            task = await repository.claim_next(lease_seconds=self._settings.worker_lease_seconds)
            if task is None:
                return False
            task_id = task.id
            execution_token = task.execution_token
            if execution_token is None:
                raise RuntimeError("claimed task has no execution token")

        try:
            claim = await self._prepare_execution(task_id, execution_token)
            if claim is None:
                return True
            outcome = await self._execute_with_heartbeat(claim)
            if not outcome.finalized:
                await self._record_success(claim, outcome.result)
        except TaskIntegrityError as exc:
            await self._record_failure(
                task_id,
                execution_token,
                str(exc),
                terminal=True,
            )
        except MemoryGovernanceConflict as exc:
            await self._record_failure(
                task_id,
                execution_token,
                str(exc),
                terminal=True,
            )
        except (PersonalityProfileConflict, PersonalityProfileDataError) as exc:
            await self._record_failure(
                task_id,
                execution_token,
                str(exc),
                terminal=True,
            )
        except IngestionError as exc:
            await self._record_failure(
                task_id,
                execution_token,
                exc.code,
                terminal=not exc.retryable,
            )
        except EmbeddingError as exc:
            await self._record_failure(
                task_id,
                execution_token,
                exc.code,
                terminal=not exc.retryable,
            )
        except ModelGovernanceError as exc:
            await self._record_failure(task_id, execution_token, str(exc), terminal=True)
        except TaskLeaseLost:
            logger.warning(
                "task_execution_abandoned_after_lease_loss",
                extra={"task_id": str(task_id)},
            )
        except Exception as exc:
            logger.exception("task_execution_failed", extra={"task_id": str(task_id)})
            await self._record_failure(
                task_id,
                execution_token,
                type(exc).__name__,
                terminal=False,
            )
        return True

    async def _prepare_execution(
        self,
        task_id: UUID,
        execution_token: UUID,
    ) -> ExecutionClaim | None:
        async with self._database.session_factory() as session, session.begin():
            repository = TaskRepository(session)
            task = await repository.get_for_worker(task_id=task_id, for_update=True)
            if (
                task is None
                or task.status != "running"
                or task.execution_token != execution_token
                or task.lease_expires_at is None
                or task.lease_expires_at <= datetime.now(UTC)
            ):
                return None

            events = EventRepository(session)
            task_service = TaskService(
                settings=self._settings,
                repository=repository,
                events=events,
                policy=self._policy,
            )
            task_service.verify_for_execution(
                task,
                allow_consumed_approval=task.attempt_count > 1,
            )
            return ExecutionClaim(
                task_id=task.id,
                execution_token=execution_token,
                owner_id=task.owner_id,
                kind=task.kind,
                trace_id=task.trace_id,
                payload=copy.deepcopy(task.payload),
            )

    async def _execute_with_heartbeat(self, claim: ExecutionClaim) -> ExecutionOutcome:
        stop = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(claim, stop, lease_lost),
            name=f"task-heartbeat-{claim.task_id}",
        )
        try:
            outcome = await self._perform_execution(claim, lease_lost)
            if lease_lost.is_set() and not outcome.finalized:
                raise TaskLeaseLost("task lease was lost during execution")
            return outcome
        finally:
            stop.set()
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat_loop(
        self,
        claim: ExecutionClaim,
        stop: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        interval = max(1.0, self._settings.worker_lease_seconds / 3)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass

            try:
                async with self._database.session_factory() as session, session.begin():
                    renewed = await TaskRepository(session).heartbeat(
                        task_id=claim.task_id,
                        execution_token=claim.execution_token,
                        lease_seconds=self._settings.worker_lease_seconds,
                    )
            except Exception:
                logger.exception(
                    "task_heartbeat_failed",
                    extra={"task_id": str(claim.task_id)},
                )
                continue
            if not renewed:
                lease_lost.set()
                logger.warning(
                    "task_lease_lost",
                    extra={"task_id": str(claim.task_id)},
                )
                return

    async def _perform_execution(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
    ) -> ExecutionOutcome:
        if (
            not self._settings.executor_security_review_approved
            and claim.kind in self._EXECUTOR_SECURITY_REVIEW_REQUIRED_KINDS
        ):
            raise TaskIntegrityError(
                f"execution blocked until security review approves {claim.kind}"
            )

        memory_without_session = MemoryService(
            settings=self._settings,
            repository=None,
            embeddings=self._embeddings,
        )

        if claim.kind == "evolution.proposal.generate":
            proposals = [str(item) for item in claim.payload["proposals"]]
            normalized = tuple(dict.fromkeys(proposals))
            source = "".join(normalized)
            generated_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
            return ExecutionOutcome(
                result={
                    "operation": "evolution.proposal.generate",
                    "proposal_count": len(normalized),
                    "proposal_ids": list(normalized),
                    "proposal_digest": generated_digest,
                }
            )

        if claim.kind == "evolution.proposal.execute":
            proposal_ids = [str(item) for item in claim.payload["proposal_ids"]]
            normalized = tuple(dict.fromkeys(proposal_ids))
            execution_seed = "|".join(normalized)
            execution_trace = hashlib.sha256(
                f"{claim.owner_id}|{execution_seed}".encode("utf-8")
            ).hexdigest()
            return ExecutionOutcome(
                result={
                    "operation": "evolution.proposal.execute",
                    "executed_count": len(normalized),
                    "operator_note": claim.payload.get("operator_note"),
                    "execution_trace": execution_trace,
                }
            )

        if claim.kind == "execution.sandbox.echo":
            input_text = str(claim.payload["input_text"])
            operation = str(claim.payload["operation"])
            if operation == "reverse":
                output_text = input_text[::-1]
            else:
                output_text = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
            return ExecutionOutcome(
                result={
                    "operation": "execution.sandbox.echo",
                    "operation_name": operation,
                    "operation_input_length": len(input_text),
                    "operation_output": output_text,
                }
            )

        if claim.kind == "memory.search":
            prepared_search = await memory_without_session.prepare_search(
                str(claim.payload["query"])
            )
            if lease_lost.is_set():
                raise TaskLeaseLost("task lease was lost before memory search")
            async with self._database.session_factory() as session, session.begin():
                memory = MemoryService(
                    settings=self._settings,
                    repository=MemoryRepository(session),
                    embeddings=self._embeddings,
                )
                hits = await memory.search_prepared(
                    owner_id=claim.owner_id,
                    prepared=prepared_search,
                    top_k=int(claim.payload["top_k"]),
                )
            return ExecutionOutcome(
                result={
                    "hits": [
                        {
                            "chunk_id": str(hit.chunk_id),
                            "document_id": str(hit.document_id),
                            "score": hit.score,
                            "title": hit.title,
                            "source_uri": hit.source_uri,
                            "text": hit.text,
                            "locator": hit.locator,
                        }
                        for hit in hits
                    ]
                }
            )

        if claim.kind == "memory.note.create":
            prepared_ingest = memory_without_session.prepare_ingest(
                owner_id=claim.owner_id,
                text=str(claim.payload["text"]),
                title=_optional_string(claim.payload.get("title")),
                sensitivity=str(claim.payload["sensitivity"]),
                retention_class=str(claim.payload["retention_class"]),
                metadata={"task_id": str(claim.task_id), "trace_id": claim.trace_id},
            )
            embedding_space = await memory_without_session.resolve_embedding_space()

            # A short lookup transaction avoids paying for embeddings on an idempotent
            # retry. No database session remains open while the GPU call runs.
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before duplicate resolution")
                existing = await MemoryService(
                    settings=self._settings,
                    repository=MemoryRepository(session),
                    embeddings=self._embeddings,
                ).resolve_existing_ingest(
                    prepared_ingest,
                    embedding_space=embedding_space,
                )
                if existing is not None:
                    result = {
                        "document_id": str(existing.document.id),
                        "created": existing.created,
                        "chunk_count": existing.chunk_count,
                    }
                    # Complete in the same transaction as the provenance observation.
                    # Raising here rolls that observation back if ownership was lost.
                    await self._finalize_local_mutation(session, claim, result)
                    return ExecutionOutcome(result=result, finalized=True)

            embedded = await memory_without_session.embed_prepared_ingest(prepared_ingest)
            if lease_lost.is_set():
                raise TaskLeaseLost("task lease was lost during document embedding")
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before document persistence")
                ingest = await MemoryService(
                    settings=self._settings,
                    repository=MemoryRepository(session),
                    embeddings=self._embeddings,
                ).persist_prepared_ingest(embedded)
                result = {
                    "document_id": str(ingest.document.id),
                    "created": ingest.created,
                    "chunk_count": ingest.chunk_count,
                }
                await self._finalize_local_mutation(session, claim, result)
            return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "memory.reindex":
            return await self._reindex_memory(claim, lease_lost, memory_without_session)

        if claim.kind == "document.ingest":
            return await self._ingest_document(claim, lease_lost, memory_without_session)

        if claim.kind == "model.candidate.register":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before model candidate registration")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.register_candidate(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "model.benchmark.suite.create":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before benchmark suite registration")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.create_benchmark_suite(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "model.benchmark.run":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before benchmark run persistence")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.record_benchmark_run(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "model.promotion.propose":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before promotion proposal creation")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.propose_promotion(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "model.activation.apply":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before activation application")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.apply_activation(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "model.rollback.apply":
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before rollback application")
                service = ModelGovernanceService(session=session, settings=self._settings)
                result = await service.apply_rollback(
                    owner_id=claim.owner_id,
                    payload=claim.payload,
                )
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        if claim.kind == "personality.profile.apply":
            try:
                proposal = normalize_profile_apply_payload(claim.payload)
            except (ValidationError, TypeError, ValueError) as exc:
                raise TaskIntegrityError("personality profile apply payload is invalid") from exc
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before personality application")
                application = await PersonalityRepository(session).apply_proposal(
                    owner_id=claim.owner_id,
                    proposal=proposal,
                    task_id=claim.task_id,
                )
                result = {
                    "applied": application.applied,
                    "feedback_id": str(proposal.feedback_id),
                    "revision": application.snapshot.revision,
                    "profile_digest": application.snapshot.profile_digest,
                }
                await self._finalize_local_mutation(session, claim, result)
                return ExecutionOutcome(result=result, finalized=True)

        # No external-side-effect executor is enabled in this release. Before one is
        # added, it must reserve an idempotency key in a durable effect ledger/outbox
        # while holding the owned-attempt lock, then reconcile that ledger on retries.
        raise TaskIntegrityError(f"worker has no executor for {claim.kind}")

    async def _reindex_memory(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
        memory_without_session: MemoryService,
    ) -> ExecutionOutcome:
        """Shadow-reindex legacy chunks without removing their prior vectors."""

        embedding_space = await memory_without_session.resolve_embedding_space()
        document_id = _optional_payload_uuid(claim.payload, "document_id")
        batch_size = int(claim.payload["batch_size"])
        processed_sources = 0
        processed_chunks = 0

        while True:
            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost during memory reindex")
                repository = MemoryRepository(session)
                chunks = await repository.list_reindex_chunks(
                    owner_id=claim.owner_id,
                    embedding_space_id=embedding_space.space_id,
                    limit=batch_size,
                    document_id=document_id,
                )
                if not chunks:
                    inventory = await repository.embedding_inventory(
                        owner_id=claim.owner_id,
                        embedding_space_id=embedding_space.space_id,
                    )
                    result = {
                        "embedding_space_id": embedding_space.space_id,
                        "model_alias": embedding_space.model_alias,
                        "model_digest": embedding_space.model_digest,
                        "reindexed_source_chunk_count": processed_sources,
                        "reindexed_chunk_count": processed_chunks,
                        "compatible_chunk_count": inventory.compatible_chunk_count,
                        "legacy_chunk_count": inventory.legacy_chunk_count,
                        "reindex_required": inventory.reindex_required,
                    }
                    await EventRepository(session).record(
                        owner_id=claim.owner_id,
                        trace_id=claim.trace_id,
                        actor="worker",
                        event_type="memory_reindexed",
                        summary=f"Reindexed {processed_chunks} memory chunks",
                        payload={
                            "task_id": str(claim.task_id),
                            "embedding_space_id": embedding_space.space_id,
                            "reindexed_source_chunk_count": processed_sources,
                            "reindexed_chunk_count": processed_chunks,
                            "legacy_chunk_count": inventory.legacy_chunk_count,
                        },
                    )
                    await self._finalize_local_mutation(session, claim, result)
                    return ExecutionOutcome(result=result, finalized=True)

            if lease_lost.is_set():
                raise TaskLeaseLost("task lease was lost before memory reindex embedding")
            embedded = await memory_without_session.embed_reindex_chunks(chunks)
            if embedded.embedding_space != embedding_space:
                raise TaskIntegrityError("embedding space changed during memory reindex")
            if lease_lost.is_set():
                raise TaskLeaseLost("task lease was lost during memory reindex embedding")

            async with self._database.session_factory() as session, session.begin():
                owned = await TaskRepository(session).lock_owned_execution(
                    task_id=claim.task_id,
                    execution_token=claim.execution_token,
                )
                if not owned:
                    raise TaskLeaseLost("task lease was lost before reindex persistence")
                applied = await MemoryRepository(session).apply_reindex_replacements(
                    owner_id=claim.owner_id,
                    replacements=list(embedded.replacements),
                    embedding_space=embedding_space,
                )
                processed_sources += applied.source_chunk_count
                processed_chunks += applied.active_chunk_count

    async def _ingest_document(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
        memory_without_session: MemoryService,
    ) -> ExecutionOutcome:
        """Execute an approved staged document in short transactional phases."""

        staging_id = _payload_uuid(claim.payload, "staging_id")
        async with self._database.session_factory() as session, session.begin():
            owned = await TaskRepository(session).lock_owned_execution(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
            )
            if not owned:
                raise TaskLeaseLost("task lease was lost before staging validation")
            staged = await DocumentStagingRepository(session).get_for_execution(
                staging_id=staging_id,
                owner_id=claim.owner_id,
                task_id=claim.task_id,
                for_update=True,
            )
            if staged is None:
                raise TaskIntegrityError("approved document staging object is missing")
            upload = _validated_staged_upload(claim, staged)

        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost before document parsing")
        extracted = await self._document_parser.extract(upload)
        context = IngestionContext(
            owner_id=claim.owner_id,
            ingestion_task_id=claim.task_id,
            sensitivity=str(claim.payload["sensitivity"]),
            retention_class=str(claim.payload["retention_class"]),
            source_timestamp=_payload_datetime(claim.payload, "source_timestamp"),
            received_at=_payload_datetime(claim.payload, "received_at"),
            source_time_basis="user_supplied",
        )
        provenance = DocumentProvenance(
            sha256=upload.content_sha256,
            original_filename=upload.original_filename,
            validated_mime_type=upload.validated_mime_type.value,
            byte_size=upload.byte_size,
            extracted_character_count=len(extracted.text),
            page_count=extracted.page_count,
            section_count=extracted.section_count,
            parser_name=extracted.parser_name,
            parser_version=extracted.parser_version,
            ingestion_task_id=context.ingestion_task_id,
            owner_id=context.owner_id,
            sensitivity=context.sensitivity,
            retention_class=context.retention_class,
            source_timestamp=context.source_timestamp,
            received_at=context.received_at,
            source_time_basis=context.source_time_basis,
        )
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost during document parsing")

        prepared_ingest = memory_without_session.prepare_ingest(
            owner_id=claim.owner_id,
            text=extracted.text,
            source_type="document",
            title=_optional_string(claim.payload.get("title")),
            mime_type=upload.validated_mime_type.value,
            sensitivity=str(claim.payload["sensitivity"]),
            retention_class=str(claim.payload["retention_class"]),
            metadata=provenance.as_metadata(),
            segments=extracted.segments,
            source_sha256=bytes.fromhex(upload.content_sha256),
        )
        embedding_space = await memory_without_session.resolve_embedding_space()

        # Resolve an idempotent retry before paying for another GPU call. The stage,
        # provenance observation, task completion, and autobiographical events commit
        # atomically if the document already exists.
        async with self._database.session_factory() as session, session.begin():
            owned = await TaskRepository(session).lock_owned_execution(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
            )
            if not owned:
                raise TaskLeaseLost("task lease was lost before duplicate resolution")
            existing = await MemoryService(
                settings=self._settings,
                repository=MemoryRepository(session),
                embeddings=self._embeddings,
            ).resolve_existing_ingest(
                prepared_ingest,
                embedding_space=embedding_space,
            )
            if existing is not None:
                result = _document_ingest_result(existing, provenance.as_metadata())
                await self._finalize_document_ingest(
                    session,
                    claim,
                    staging_id=staging_id,
                    result=result,
                )
                return ExecutionOutcome(result=result, finalized=True)

        embedded = await memory_without_session.embed_prepared_ingest(prepared_ingest)
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost during document embedding")
        async with self._database.session_factory() as session, session.begin():
            owned = await TaskRepository(session).lock_owned_execution(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
            )
            if not owned:
                raise TaskLeaseLost("task lease was lost before document persistence")
            ingest = await MemoryService(
                settings=self._settings,
                repository=MemoryRepository(session),
                embeddings=self._embeddings,
            ).persist_prepared_ingest(embedded)
            result = _document_ingest_result(ingest, provenance.as_metadata())
            await self._finalize_document_ingest(
                session,
                claim,
                staging_id=staging_id,
                result=result,
            )
        return ExecutionOutcome(result=result, finalized=True)

    async def _finalize_document_ingest(
        self,
        session: AsyncSession,
        claim: ExecutionClaim,
        *,
        staging_id: UUID,
        result: dict[str, Any],
    ) -> None:
        deleted = await DocumentStagingRepository(session).delete_for_execution(
            staging_id=staging_id,
            owner_id=claim.owner_id,
            task_id=claim.task_id,
        )
        if not deleted:
            raise TaskIntegrityError("document staging object disappeared before finalization")
        await EventRepository(session).record(
            owner_id=claim.owner_id,
            trace_id=claim.trace_id,
            actor="worker",
            event_type="document_ingested",
            summary="Ingested an approved document",
            payload={
                "task_id": str(claim.task_id),
                "document_id": result["document_id"],
                "created": result["created"],
                "chunk_count": result["chunk_count"],
                "mime_type": result["provenance"]["validated_mime_type"],
                "source_sha256": result["provenance"]["sha256"],
            },
        )
        await self._finalize_local_mutation(session, claim, result)

    async def _finalize_local_mutation(
        self,
        session: AsyncSession,
        claim: ExecutionClaim,
        result: dict[str, Any],
    ) -> None:
        transition = await TaskRepository(session).mark_done_if_owned(
            task_id=claim.task_id,
            execution_token=claim.execution_token,
            result=result,
        )
        if transition is None:
            raise TaskLeaseLost("task lease was lost before atomic effect completion")
        await EventRepository(session).record(
            owner_id=transition.owner_id,
            trace_id=transition.trace_id,
            actor="worker",
            event_type="task_completed",
            summary=f"Completed {transition.kind} task",
            payload={"task_id": str(transition.task_id)},
        )

    async def _record_success(
        self,
        claim: ExecutionClaim,
        result: dict[str, Any],
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            transition = await TaskRepository(session).mark_done_if_owned(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
                result=result,
            )
            if transition is None:
                logger.warning(
                    "stale_task_completion_rejected",
                    extra={"task_id": str(claim.task_id)},
                )
                return
            await EventRepository(session).record(
                owner_id=transition.owner_id,
                trace_id=transition.trace_id,
                actor="worker",
                event_type="task_completed",
                summary=f"Completed {transition.kind} task",
                payload={"task_id": str(transition.task_id)},
            )

    async def _record_failure(
        self,
        task_id: UUID,
        execution_token: UUID,
        error: str,
        *,
        terminal: bool,
    ) -> None:
        async with self._database.session_factory() as session, session.begin():
            repository = TaskRepository(session)
            transition = await repository.mark_failed_if_owned(
                task_id=task_id,
                execution_token=execution_token,
                error=error,
                terminal=terminal,
            )
            if transition is None:
                logger.warning(
                    "stale_task_failure_rejected",
                    extra={"task_id": str(task_id)},
                )
                return
            if transition.status == "failed" and transition.kind == "document.ingest":
                await DocumentStagingRepository(session).delete_for_task(
                    owner_id=transition.owner_id,
                    task_id=transition.task_id,
                )
                await EventRepository(session).record(
                    owner_id=transition.owner_id,
                    trace_id=transition.trace_id,
                    actor="worker",
                    event_type="document_ingest_failed",
                    summary="Document ingestion failed",
                    payload={
                        "task_id": str(transition.task_id),
                        "error_code": error[:100],
                    },
                )
            await EventRepository(session).record(
                owner_id=transition.owner_id,
                trace_id=transition.trace_id,
                actor="worker",
                event_type=("task_failed" if transition.status == "failed" else "task_requeued"),
                summary=f"{transition.kind} execution failed",
                payload={"task_id": str(transition.task_id), "error_type": error[:100]},
            )

    async def run_forever(self, stop: asyncio.Event) -> None:
        runtime_heartbeat = asyncio.create_task(
            self._runtime_heartbeat.run(stop),
            name="worker-runtime-heartbeat",
        )
        try:
            while not stop.is_set():
                processed = await self.run_once()
                if not processed:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(
                            stop.wait(),
                            timeout=self._settings.worker_poll_seconds,
                        )
        finally:
            runtime_heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await runtime_heartbeat


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _payload_uuid(payload: dict[str, Any], key: str) -> UUID:
    value = payload.get(key)
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise TaskIntegrityError(f"document task {key} is invalid") from exc


def _optional_payload_uuid(payload: dict[str, Any], key: str) -> UUID | None:
    value = payload.get(key)
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise TaskIntegrityError(f"task {key} is invalid") from exc


def _payload_datetime(payload: dict[str, Any], key: str) -> datetime:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TaskIntegrityError(f"document task {key} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TaskIntegrityError(f"document task {key} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskIntegrityError(f"document task {key} is invalid")
    return parsed.astimezone(UTC)


def _validated_staged_upload(
    claim: ExecutionClaim,
    staged: DocumentStaging,
) -> ValidatedUpload:
    digest = hashlib.sha256(staged.content).hexdigest()
    expected_timestamp = _payload_datetime(claim.payload, "source_timestamp")
    expected_received_at = _payload_datetime(claim.payload, "received_at")
    actual_timestamp = staged.source_timestamp.astimezone(UTC)
    actual_received_at = staged.received_at.astimezone(UTC)
    if (
        claim.payload.get("original_filename") != staged.original_filename
        or claim.payload.get("source_sha256") != staged.source_sha256.hex()
        or claim.payload.get("detected_mime_type") != staged.detected_mime_type
        or claim.payload.get("byte_size") != staged.byte_size
        or expected_timestamp != actual_timestamp
        or expected_received_at != actual_received_at
        or claim.payload.get("source_time_basis") != "user_supplied"
        or digest != staged.source_sha256.hex()
    ):
        raise TaskIntegrityError("approved document metadata does not match staged content")
    try:
        media_type = DocumentMediaType(staged.detected_mime_type)
    except ValueError as exc:
        raise TaskIntegrityError("staged document media type is invalid") from exc
    return ValidatedUpload(
        original_filename=staged.original_filename,
        validated_mime_type=media_type,
        content_sha256=digest,
        byte_size=staged.byte_size,
        content=bytes(staged.content),
    )


def _document_ingest_result(
    ingest: IngestResult,
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {
        "document_id": str(ingest.document.id),
        "created": ingest.created,
        "chunk_count": ingest.chunk_count,
        "provenance": provenance,
    }


async def _async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    inference = OllamaBackend(
        base_url=settings.ollama_base_url,
        chat_model=settings.ollama_chat_model,
        embedding_model=settings.ollama_embedding_model,
        think=settings.ollama_think,
        timeout=settings.inference_timeout_seconds,
        health_timeout=settings.inference_health_timeout_seconds,
    )
    embeddings = EmbeddingService(
        provider=OllamaEmbeddingProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_embedding_model,
            dimension=settings.embedding_dimensions,
            timeout=settings.inference_timeout_seconds,
            max_input_bytes=settings.embedding_max_input_bytes,
        ),
        expected_dimension=settings.embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        max_text_bytes=settings.embedding_max_input_bytes,
        expected_model_digest=settings.ollama_embedding_model_digest,
    )
    document_parser = document_parser_from_settings(settings)
    worker = Worker(
        settings=settings,
        database=database,
        inference=inference,
        embeddings=embeddings,
        document_parser=document_parser,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await worker.run_forever(stop)
    finally:
        await document_parser.aclose()
        await embeddings.aclose()
        await inference.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    run()
