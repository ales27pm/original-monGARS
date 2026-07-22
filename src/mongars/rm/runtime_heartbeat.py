"""Durable worker capability heartbeats with short persistence transactions."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import Insert, insert

from mongars.config import Settings
from mongars.db.models import RuntimeComponent
from mongars.db.session import Database
from mongars.embeddings.models import EmbeddingSpace
from mongars.embeddings.service import EmbeddingService
from mongars.ingestion.isolation import DocumentParser, ParserHealth
from mongars.runtime import RuntimeReportedStatus

logger = logging.getLogger(__name__)

_SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_PARSER_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


@dataclass(frozen=True, slots=True)
class WorkerRuntimeObservation:
    """Detached, non-sensitive capability data collected before persistence."""

    parser: ParserHealth
    embedding_space: EmbeddingSpace | None

    @property
    def status(self) -> RuntimeReportedStatus:
        if self.parser.healthy and self.embedding_space is not None:
            return "healthy"
        return "degraded"

    def capabilities(self) -> dict[str, Any]:
        space = self.embedding_space
        return {
            "document_parser": {
                "healthy": self.parser.healthy,
                "parser_version": self.parser.parser_version,
                "error_code": self.parser.error_code,
            },
            "embedding_space": {
                "space_id": space.space_id if space is not None else None,
                "model_alias": space.model_alias if space is not None else None,
                "model_digest": space.model_digest if space is not None else None,
                "dimension": space.dimension if space is not None else None,
            },
        }


class WorkerRuntimeHeartbeat:
    """Probe worker-owned capabilities and publish one durable process heartbeat."""

    def __init__(
        self,
        *,
        settings: Settings,
        database: Database,
        embeddings: EmbeddingService,
        document_parser: DocumentParser,
        instance_id: UUID | None = None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._embeddings = embeddings
        self._document_parser = document_parser
        self._instance_id = instance_id or uuid4()
        self._component_id = "worker:primary"

    async def publish_once(self) -> bool:
        """Collect outside a transaction, then attempt one short upsert transaction."""

        parser, embedding_space = await asyncio.gather(
            self._probe_parser(),
            self._probe_embedding_space(),
        )
        observation = WorkerRuntimeObservation(
            parser=parser,
            embedding_space=embedding_space,
        )
        try:
            statement = _heartbeat_upsert(
                component_id=self._component_id,
                instance_id=self._instance_id,
                version=self._settings.runtime_version,
                git_sha=self._settings.runtime_git_sha,
                status=observation.status,
                capabilities=observation.capabilities(),
            )
            async with self._database.session_factory.begin() as session:
                await session.execute(statement)
        except Exception as exc:
            # Exception messages and tracebacks may contain a database URL. Record only
            # the non-sensitive exception class and keep the worker alive.
            logger.warning(
                "worker_runtime_heartbeat_publish_failed",
                extra={"error_type": type(exc).__name__},
            )
            return False
        return True

    async def run(self, stop: asyncio.Event) -> None:
        """Publish immediately and periodically until the worker is stopped."""

        while not stop.is_set():
            await self.publish_once()
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._settings.worker_runtime_heartbeat_seconds,
                )
            except TimeoutError:
                continue

    async def _probe_parser(self) -> ParserHealth:
        try:
            health = await self._document_parser.health()
            return _canonical_parser_health(health)
        except Exception as exc:
            logger.warning(
                "worker_runtime_parser_probe_failed",
                extra={"error_type": type(exc).__name__},
            )
            return ParserHealth(
                healthy=False,
                parser_version=None,
                error_code="parser_probe_failed",
            )

    async def _probe_embedding_space(self) -> EmbeddingSpace | None:
        try:
            space = await self._embeddings.resolve_space()
            probe = await self._embeddings.embed(
                ["monGARS embedding readiness probe"],
                purpose="classification",
            )
            if probe.embedding_space_id != space.space_id:
                raise RuntimeError("embedding readiness probe changed semantic space")
            return space
        except Exception as exc:
            logger.warning(
                "worker_runtime_embedding_probe_failed",
                extra={"error_type": type(exc).__name__},
            )
            return None


def _canonical_parser_health(health: ParserHealth) -> ParserHealth:
    version = health.parser_version
    version_is_valid = (
        isinstance(version, str) and _SAFE_PARSER_VERSION.fullmatch(version) is not None
    )
    if health.healthy and version_is_valid:
        return ParserHealth(healthy=True, parser_version=version, error_code=None)

    error_code = health.error_code
    if not isinstance(error_code, str) or _SAFE_ERROR_CODE.fullmatch(error_code) is None:
        error_code = "parser_health_invalid" if health.healthy else "parser_unhealthy"
    return ParserHealth(
        healthy=False,
        parser_version=version if version_is_valid else None,
        error_code=error_code,
    )


def _heartbeat_upsert(
    *,
    component_id: str,
    instance_id: UUID,
    version: str,
    git_sha: str,
    status: RuntimeReportedStatus,
    capabilities: dict[str, Any],
) -> Insert:
    statement = insert(RuntimeComponent).values(
        component_id=component_id,
        instance_id=instance_id,
        component_type="worker",
        version=version,
        git_sha=git_sha,
        status=status,
        capabilities=capabilities,
        last_seen_at=func.now(),
    )
    return statement.on_conflict_do_update(
        index_elements=[RuntimeComponent.component_id],
        set_={
            "instance_id": statement.excluded.instance_id,
            "component_type": statement.excluded.component_type,
            "version": statement.excluded.version,
            "git_sha": statement.excluded.git_sha,
            "status": statement.excluded.status,
            "capabilities": statement.excluded.capabilities,
            "last_seen_at": func.now(),
        },
    )


__all__ = ["WorkerRuntimeHeartbeat", "WorkerRuntimeObservation"]
