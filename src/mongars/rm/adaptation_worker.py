"""Worker runtime extension for approval-gated personality profile mutations."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from mongars.adaptation.lifecycle import (
    PersonalityLifecycleRepository,
    PersonalityProfileLifecycleConflict,
    PersonalityProfileLifecycleDataError,
)
from mongars.adaptation.mimicry import profile_delta_proposal_from_payload
from mongars.adaptation.repository import (
    PersonalityProfileConflict,
    PersonalityProfileDataError,
    PersonalityRepository,
)
from mongars.config import Settings, get_settings
from mongars.db.session import Database
from mongars.embeddings.ollama import OllamaEmbeddingProvider
from mongars.embeddings.service import EmbeddingService
from mongars.events.repository import EventRepository
from mongars.inference.base import InferenceBackend
from mongars.inference.ollama import OllamaBackend
from mongars.ingestion.isolation import DocumentParser
from mongars.ingestion.runtime import document_parser_from_settings
from mongars.logging import configure_logging
from mongars.rm.contracts import normalize_task_payload
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskIntegrityError
from mongars.rm.worker import (
    ExecutionClaim,
    ExecutionOutcome,
    TaskLeaseLost,
    Worker,
)
from mongars.security.runtime_policy import build_control_plane_policy


class AdaptationWorker(Worker):
    """Run the core worker plus reviewed local personality mutation executors."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        inference: InferenceBackend,
        embeddings: EmbeddingService,
        document_parser: DocumentParser | None = None,
    ) -> None:
        super().__init__(
            settings=settings,
            database=database,
            inference=inference,
            embeddings=embeddings,
            document_parser=document_parser,
        )
        self._policy = build_control_plane_policy()

    async def _perform_execution(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
    ) -> ExecutionOutcome:
        if claim.kind == "personality.profile.apply":
            return await self._apply_profile(claim, lease_lost)
        if claim.kind == "personality.profile.reset":
            return await self._reset_profile(claim, lease_lost)
        if claim.kind == "personality.profile.delete":
            return await self._delete_profile(claim, lease_lost)
        return await super()._perform_execution(claim, lease_lost)

    async def _apply_profile(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
    ) -> ExecutionOutcome:
        try:
            proposal = profile_delta_proposal_from_payload(claim.payload)
        except (TypeError, ValueError) as exc:
            raise TaskIntegrityError("personality profile task payload is invalid") from exc
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost before personality profile application")

        async with self._database.session_factory() as session, session.begin():
            await PersonalityLifecycleRepository(session).lock_owner(
                owner_id=claim.owner_id
            )
            await _require_owned_execution(session, claim)
            try:
                application = await PersonalityRepository(session).apply_proposal(
                    owner_id=claim.owner_id,
                    proposal=proposal,
                    task_id=claim.task_id,
                )
            except (PersonalityProfileConflict, PersonalityProfileDataError) as exc:
                raise TaskIntegrityError(str(exc)) from exc

            result = {
                "applied": application.applied,
                "profile_revision": application.snapshot.revision,
                "profile_digest": application.snapshot.profile_digest,
                "changed_dimension": proposal.changed_dimension,
                "conflict": proposal.conflict,
            }
            await EventRepository(session).record(
                owner_id=claim.owner_id,
                trace_id=claim.trace_id,
                actor="worker",
                event_type="personality_profile_applied",
                summary="Applied an approved personality preference update",
                payload={
                    "task_id": str(claim.task_id),
                    "feedback_id": str(proposal.feedback_id),
                    "profile_revision": application.snapshot.revision,
                    "profile_digest": application.snapshot.profile_digest,
                    "changed_dimension": proposal.changed_dimension,
                    "conflict": proposal.conflict,
                    "applied": application.applied,
                },
            )
            await self._finalize_local_mutation(session, claim, result)
        return ExecutionOutcome(result=result, finalized=True)

    async def _reset_profile(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
    ) -> ExecutionOutcome:
        payload = _normalized_lifecycle_payload(claim)
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost before personality profile reset")

        async with self._database.session_factory() as session, session.begin():
            await PersonalityLifecycleRepository(session).lock_owner(
                owner_id=claim.owner_id
            )
            await _require_owned_execution(session, claim)
            try:
                application = await PersonalityLifecycleRepository(session).reset_profile(
                    owner_id=claim.owner_id,
                    expected_revision=cast(int, payload["expected_revision"]),
                    expected_profile_digest=cast(str, payload["expected_profile_digest"]),
                    target_revision=cast(int, payload["target_revision"]),
                    target_profile_digest=cast(str, payload["target_profile_digest"]),
                    task_id=claim.task_id,
                )
            except (
                PersonalityProfileLifecycleConflict,
                PersonalityProfileLifecycleDataError,
                PersonalityProfileDataError,
            ) as exc:
                raise TaskIntegrityError(str(exc)) from exc

            result = {
                "applied": application.applied,
                "profile_revision": application.snapshot.revision,
                "profile_digest": application.snapshot.profile_digest,
                "preference_count": 0,
            }
            await EventRepository(session).record(
                owner_id=claim.owner_id,
                trace_id=claim.trace_id,
                actor="worker",
                event_type="personality_profile_reset",
                summary="Reset the personality profile after exact-payload approval",
                payload={
                    "task_id": str(claim.task_id),
                    "profile_revision": application.snapshot.revision,
                    "profile_digest": application.snapshot.profile_digest,
                    "applied": application.applied,
                },
            )
            await self._finalize_local_mutation(session, claim, result)
        return ExecutionOutcome(result=result, finalized=True)

    async def _delete_profile(
        self,
        claim: ExecutionClaim,
        lease_lost: asyncio.Event,
    ) -> ExecutionOutcome:
        payload = _normalized_lifecycle_payload(claim)
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost before personality data deletion")

        async with self._database.session_factory() as session, session.begin():
            lifecycle = PersonalityLifecycleRepository(session)
            await lifecycle.lock_owner(owner_id=claim.owner_id)
            # Take table locks before the current task row. The heartbeat updates task_queue in a
            # separate transaction, so reversing this order can deadlock row and table locks.
            await lifecycle.lock_deletion_writes()
            await _require_owned_execution(session, claim)
            try:
                application = await lifecycle.delete_profile_data(
                    owner_id=claim.owner_id,
                    expected_revision=cast(int, payload["expected_revision"]),
                    expected_profile_digest=cast(str, payload["expected_profile_digest"]),
                    expected_data_state_digest=cast(str, payload["data_state_digest"]),
                    task_id=claim.task_id,
                    trace_id=claim.trace_id,
                )
            except (
                PersonalityProfileLifecycleConflict,
                PersonalityProfileLifecycleDataError,
                PersonalityProfileDataError,
            ) as exc:
                raise TaskIntegrityError(str(exc)) from exc

            result = {
                "applied": application.applied,
                "profile_revision": 0,
                "deleted_feedback": application.deleted_feedback,
                "deleted_revisions": application.deleted_revisions,
                "deleted_tasks": application.deleted_tasks,
                "deleted_events": application.deleted_events,
            }
            await EventRepository(session).record(
                owner_id=claim.owner_id,
                trace_id=claim.trace_id,
                actor="worker",
                event_type="personality_profile_deleted",
                summary="Deleted owner personality data after exact-payload approval",
                payload={
                    "task_id": str(claim.task_id),
                    "profile_revision": 0,
                    "deleted_feedback": application.deleted_feedback,
                    "deleted_revisions": application.deleted_revisions,
                    "deleted_tasks": application.deleted_tasks,
                    "deleted_events": application.deleted_events,
                    "applied": application.applied,
                },
            )
            await self._finalize_local_mutation(session, claim, result)
        return ExecutionOutcome(result=result, finalized=True)


def _normalized_lifecycle_payload(claim: ExecutionClaim) -> dict[str, object]:
    try:
        return cast(dict[str, object], normalize_task_payload(claim.kind, claim.payload))
    except (TypeError, ValueError) as exc:
        raise TaskIntegrityError("personality lifecycle task payload is invalid") from exc


async def _require_owned_execution(
    session: AsyncSession,
    claim: ExecutionClaim,
) -> None:
    owned = await TaskRepository(session).lock_owned_execution(
        task_id=claim.task_id,
        execution_token=claim.execution_token,
    )
    if not owned:
        raise TaskLeaseLost("task lease was lost before personality profile persistence")


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
    worker = AdaptationWorker(
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
        if document_parser is not None:
            await document_parser.aclose()
        await embeddings.aclose()
        await inference.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    run()
