from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import SecretStr

from mongars.security.auth import AuthenticatedPrincipal, BearerTokenAuth


def _settings(token: str) -> SimpleNamespace:
    return SimpleNamespace(api_token=SecretStr(token), environment="test")


def _credentials(token: str, scheme: str = "Bearer") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


@pytest.mark.asyncio
async def test_valid_bearer_token_returns_server_derived_principal() -> None:
    authenticate = BearerTokenAuth(_settings("correct-horse-battery-staple"), subject="owner-1")

    principal = await authenticate(_credentials("correct-horse-battery-staple"))

    assert principal == AuthenticatedPrincipal(subject="owner-1", environment="test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "credentials",
    [
        None,
        HTTPAuthorizationCredentials(scheme="Basic", credentials="irrelevant"),
        _credentials("wrong-token"),
        _credentials("\N{SNOWMAN}"),
        _credentials(""),
    ],
)
async def test_missing_or_invalid_credentials_are_rejected(
    credentials: HTTPAuthorizationCredentials | None,
) -> None:
    authenticate = BearerTokenAuth(_settings("correct-horse-battery-staple"))

    with pytest.raises(HTTPException) as exc_info:
        await authenticate(credentials)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}
    assert exc_info.value.detail == "Invalid or missing bearer token"


@pytest.mark.asyncio
async def test_token_comparison_uses_compare_digest() -> None:
    authenticate = BearerTokenAuth(_settings("correct-horse-battery-staple"))

    with patch("mongars.security.auth.compare_digest", return_value=True) as compare:
        await authenticate(_credentials("attacker-controlled"))

    compare.assert_called_once_with(
        b"attacker-controlled",
        b"correct-horse-battery-staple",
    )


def test_empty_configured_token_fails_during_startup() -> None:
    with pytest.raises(ValueError, match="api_token must not be empty"):
        BearerTokenAuth(_settings(""))
