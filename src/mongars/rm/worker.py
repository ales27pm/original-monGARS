from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import suppress
from time import monotonic
from typing import Any
from uuid import UUID

from mongars.config import Settings, get_settings
from mongars.db.session import Database
from mongars.events.repository import EventRepository
from mongars.inference.base import InferenceBackend
from mongars.inference.ollama import OllamaBackend
from mongars.logging import configure_logging
from mongars.memory.repository import MemoryRepository
from mongars.memory.service import MemoryService
from mongars.rm.repository import TaskRepository
from mongars.rm.service import TaskIntegrityError, TaskService
from mongars.security.policy import ActionClassification, ToolPolicy

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        inference: InferenceBackend,
    ) -> None:
        self._settings = settings
        self._database = database
        self._inference = inference
        self._next_retention_sweep = 0.0
        self._policy = ToolPolicy(
            {
                ("memory", "search"): ActionClassification.READ_ONLY,
                ("memory", "note.create"): ActionClassification.LOCAL_MUTATION,
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
            await repository.recover_expired_leases()
            task = await repository.claim_next(lease_seconds=self._settings.worker_lease_seconds)
            if task is None:
                return False
            task_id = task.id

        try:
            await self._execute(task_id)
        except TaskIntegrityError as exc:
            await self._record_failure(task_id, str(exc), terminal=True)
        except Exception as exc:
            logger.exception("task_execution_failed", extra={"task_id": str(task_id)})
            await self._record_failure(task_id, type(exc).__name__, terminal=False)
        return True

    async def _execute(self, task_id: UUID) -> None:
        async with self._database.session_factory() as session, session.begin():
            repository = TaskRepository(session)
            task = await repository.get_for_worker(task_id=task_id, for_update=True)
            if task is None or task.status != "running":
                return

            events = EventRepository(session)
            task_service = TaskService(
                settings=self._settings,
                repository=repository,
                events=events,
                policy=self._policy,
            )
            task_service.verify_for_execution(task)
            memory = MemoryService(
                settings=self._settings,
                repository=MemoryRepository(session),
                inference=self._inference,
            )

            result: dict[str, Any]
            if task.kind == "memory.search":
                hits = await memory.search(
                    owner_id=task.owner_id,
                    query=str(task.payload["query"]),
                    top_k=int(task.payload["top_k"]),
                )
                result = {
                    "hits": [
                        {
                            "chunk_id": str(hit.chunk_id),
                            "document_id": str(hit.document_id),
                            "score": hit.score,
                            "title": hit.title,
                            "source_uri": hit.source_uri,
                            "text": hit.text,
                        }
                        for hit in hits
                    ]
                }
            elif task.kind == "memory.note.create":
                ingest = await memory.ingest_text(
                    owner_id=task.owner_id,
                    text=str(task.payload["text"]),
                    title=_optional_string(task.payload.get("title")),
                    sensitivity=str(task.payload["sensitivity"]),
                    retention_class=str(task.payload["retention_class"]),
                    metadata={"task_id": str(task.id), "trace_id": task.trace_id},
                )
                result = {
                    "document_id": str(ingest.document.id),
                    "created": ingest.created,
                    "chunk_count": ingest.chunk_count,
                }
            else:
                raise TaskIntegrityError(f"worker has no executor for {task.kind}")

            await repository.mark_done(task, result=result)
            await events.record(
                owner_id=task.owner_id,
                trace_id=task.trace_id,
                actor="worker",
                event_type="task_completed",
                summary=f"Completed {task.kind} task",
                payload={"task_id": str(task.id)},
            )

    async def _record_failure(self, task_id: UUID, error: str, *, terminal: bool) -> None:
        async with self._database.session_factory() as session, session.begin():
            repository = TaskRepository(session)
            task = await repository.get_for_worker(task_id=task_id, for_update=True)
            if task is None or task.status != "running":
                return
            if terminal:
                await repository.mark_terminal_failed(task, error=error)
            else:
                await repository.mark_failed(task, error=error)
            await EventRepository(session).record(
                owner_id=task.owner_id,
                trace_id=task.trace_id,
                actor="worker",
                event_type="task_failed" if task.status == "failed" else "task_retried",
                summary=f"{task.kind} execution failed",
                payload={"task_id": str(task.id), "error_type": error[:100]},
            )

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            processed = await self.run_once()
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=self._settings.worker_poll_seconds)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


async def _async_main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    inference = OllamaBackend(
        base_url=settings.ollama_base_url,
        chat_model=settings.ollama_chat_model,
        embedding_model=settings.ollama_embedding_model,
        embedding_dimension=settings.embedding_dimensions,
        think=settings.ollama_think,
        timeout=settings.inference_timeout_seconds,
        health_timeout=settings.inference_health_timeout_seconds,
    )
    worker = Worker(settings=settings, database=database, inference=inference)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop.set)
    try:
        await worker.run_forever(stop)
    finally:
        await inference.aclose()
        await database.close()


def run() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    run()
