"""Worker runtime extension for approval-gated personality profile application."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress

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
    """Run the core worker plus the reviewed local personality mutation executor."""

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
        if claim.kind != "personality.profile.apply":
            return await super()._perform_execution(claim, lease_lost)

        try:
            proposal = profile_delta_proposal_from_payload(claim.payload)
        except (TypeError, ValueError) as exc:
            raise TaskIntegrityError("personality profile task payload is invalid") from exc
        if lease_lost.is_set():
            raise TaskLeaseLost("task lease was lost before personality profile application")

        async with self._database.session_factory() as session, session.begin():
            owned = await TaskRepository(session).lock_owned_execution(
                task_id=claim.task_id,
                execution_token=claim.execution_token,
            )
            if not owned:
                raise TaskLeaseLost("task lease was lost before personality profile persistence")
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
