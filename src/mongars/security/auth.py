"""Explicit bearer-token authentication dependencies.

Authentication is deliberately exposed as a route dependency instead of global
middleware.  Protected routes opt in to :class:`BearerTokenAuth`, while
liveness and readiness routes can remain unauthenticated.
"""

from __future__ import annotations

from dataclasses import dataclass
from hmac import compare_digest
from typing import Annotated, NoReturn, Protocol

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import SecretStr


class AuthSettings(Protocol):
    """The subset of application settings needed by authentication."""

    @property
    def api_token(self) -> SecretStr: ...

    @property
    def environment(self) -> object: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Server-derived identity for the single-owner local deployment."""

    subject: str
    environment: str


bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


class BearerTokenAuth:
    """Validate a configured bearer token and return a server-owned principal.

    A separate instance should be constructed from application settings and
    attached only to protected FastAPI routes using ``Depends`` or ``Security``.
    The expected credential is copied as UTF-8 bytes so comparisons can use
    :func:`hmac.compare_digest`, including when an attacker submits non-ASCII
    input.
    """

    def __init__(self, settings: AuthSettings, *, subject: str = "local-owner") -> None:
        expected_token = settings.api_token.get_secret_value()
        if not expected_token:
            raise ValueError("api_token must not be empty")
        if not subject:
            raise ValueError("subject must not be empty")

        self._expected_token = expected_token.encode("utf-8")
        self._principal = AuthenticatedPrincipal(
            subject=subject,
            environment=str(settings.environment),
        )

    async def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Security(bearer_scheme),
        ] = None,
    ) -> AuthenticatedPrincipal:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            _unauthorized()

        supplied_token = credentials.credentials
        if not supplied_token:
            _unauthorized()

        if not compare_digest(
            supplied_token.encode("utf-8"),
            self._expected_token,
        ):
            _unauthorized()

        return self._principal
