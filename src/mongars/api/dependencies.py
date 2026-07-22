from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Coroutine
from threading import Lock
from typing import Annotated, Any, cast

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from mongars.config import Settings
from mongars.db.session import Database
from mongars.embeddings.service import EmbeddingService
from mongars.inference.base import InferenceBackend
from mongars.ingestion.concurrency import (
    DocumentUploadAdmissionController,
    DocumentUploadPermit,
)
from mongars.security.auth import AuthenticatedPrincipal, BearerTokenAuth, bearer_scheme
from mongars.security.policy import ToolPolicy
from mongars.web_search import SearxNGSearchBackend

_UPLOAD_ADMISSION_INITIALIZATION_LOCK = Lock()


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_inference(request: Request) -> InferenceBackend:
    return cast(InferenceBackend, request.app.state.inference)


def get_embeddings(request: Request) -> EmbeddingService:
    return cast(EmbeddingService, request.app.state.embeddings)


def get_policy(request: Request) -> ToolPolicy:
    return cast(ToolPolicy, request.app.state.policy)


def get_web_search(request: Request) -> SearxNGSearchBackend | None:
    return cast(SearxNGSearchBackend | None, request.app.state.web_search)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    database = cast(Database, request.app.state.database)
    async with database.session_factory() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def require_principal(
    request: Request,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Security(bearer_scheme),
    ] = None,
) -> AuthenticatedPrincipal:
    auth = cast(BearerTokenAuth, request.app.state.auth)
    return await auth(credentials)


def get_document_upload_admission(request: Request) -> DocumentUploadAdmissionController:
    """Return one immutable process-local admission controller per FastAPI app."""

    settings = get_runtime_settings(request)
    existing = getattr(request.app.state, "document_upload_admission", None)
    if existing is None:
        with _UPLOAD_ADMISSION_INITIALIZATION_LOCK:
            existing = getattr(request.app.state, "document_upload_admission", None)
            if existing is None:
                existing = DocumentUploadAdmissionController(
                    global_limit=settings.max_concurrent_document_uploads,
                    per_owner_limit=settings.max_concurrent_document_uploads_per_owner,
                )
                request.app.state.document_upload_admission = existing
    if not isinstance(existing, DocumentUploadAdmissionController):
        raise RuntimeError("document upload admission state has an invalid type")
    if (
        existing.global_limit != settings.max_concurrent_document_uploads
        or existing.per_owner_limit != settings.max_concurrent_document_uploads_per_owner
    ):
        raise RuntimeError("document upload admission limits changed after initialization")
    return existing


def get_document_upload_permit(request: Request) -> DocumentUploadPermit:
    permit = getattr(request.state, "document_upload_permit", None)
    if not isinstance(permit, DocumentUploadPermit):
        raise RuntimeError("document upload route has no admission permit")
    return permit


def _authorization_credentials(request: Request) -> HTTPAuthorizationCredentials | None:
    raw_value = request.headers.get("Authorization", "")
    parts = raw_value.split(None, 1)
    if len(parts) != 2:
        return None
    return HTTPAuthorizationCredentials(scheme=parts[0], credentials=parts[1])


class DocumentUploadAdmissionRoute(APIRoute):
    """Authenticate and admit a document upload before FastAPI reads multipart bytes."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def admitted_route_handler(request: Request) -> Response:
            auth = cast(BearerTokenAuth, request.app.state.auth)
            principal = await auth(_authorization_credentials(request))
            admission = get_document_upload_admission(request)
            permit = admission.try_acquire(owner_id=principal.subject)
            if permit is None:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="document upload concurrency limit reached",
                    headers={"Retry-After": "1"},
                )
            request.state.document_upload_permit = permit
            try:
                return await route_handler(request)
            finally:
                # Synchronous, idempotent release cannot be interrupted by cancellation.
                permit.release()
                del request.state.document_upload_permit

        return admitted_route_handler


# Finalize the transaction before Starlette sends the response. Request-scoped yield
# cleanup runs after response delivery, which lets an immediate follow-up request observe
# a just-created task as missing even though the create response already returned 202.
SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(require_principal)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
InferenceDependency = Annotated[InferenceBackend, Depends(get_inference)]
EmbeddingsDependency = Annotated[EmbeddingService, Depends(get_embeddings)]
PolicyDependency = Annotated[ToolPolicy, Depends(get_policy)]
WebSearchDependency = Annotated[SearxNGSearchBackend | None, Depends(get_web_search)]
DocumentUploadAdmissionDependency = Annotated[
    DocumentUploadPermit,
    Depends(get_document_upload_permit),
]
