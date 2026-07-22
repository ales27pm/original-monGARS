from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from mongars.config import Settings
from mongars.db.session import Database
from mongars.inference.base import InferenceBackend
from mongars.security.auth import AuthenticatedPrincipal, BearerTokenAuth, bearer_scheme
from mongars.security.policy import ToolPolicy
from mongars.web_search import SearxNGSearchBackend


def get_runtime_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_inference(request: Request) -> InferenceBackend:
    return cast(InferenceBackend, request.app.state.inference)


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


# Finalize the transaction before Starlette sends the response. Request-scoped yield
# cleanup runs after response delivery, which lets an immediate follow-up request observe
# a just-created task as missing even though the create response already returned 202.
SessionDependency = Annotated[AsyncSession, Depends(get_session, scope="function")]
PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(require_principal)]
SettingsDependency = Annotated[Settings, Depends(get_runtime_settings)]
InferenceDependency = Annotated[InferenceBackend, Depends(get_inference)]
PolicyDependency = Annotated[ToolPolicy, Depends(get_policy)]
WebSearchDependency = Annotated[SearxNGSearchBackend | None, Depends(get_web_search)]
