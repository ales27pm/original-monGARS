from __future__ import annotations

from datetime import UTC, datetime, timedelta

import base64
import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from mongars.api.routes import p2p
from mongars.config import Environment, Settings
from mongars.security.auth import BearerTokenAuth

_AUTH_VALUE = "unit-p2p-route-token"
_AUTH_HEADERS = {"Authorization": f"Bearer {_AUTH_VALUE}"}


def _configure_auth(application: FastAPI, settings: Settings) -> None:
    application.state.settings = settings
    application.state.auth = BearerTokenAuth(settings, subject=settings.owner_id)


def _build_app() -> tuple[FastAPI, Settings]:
    application = FastAPI()
    settings = Settings(
        environment=Environment.TEST,
        api_token=SecretStr(_AUTH_VALUE),
        owner_id="owner-local",
    )
    _configure_auth(application, settings)
    application.include_router(p2p.router)
    return application, settings


def _pair_request(*, peer_id: str) -> dict[str, str]:
    secret = base64.b64encode(b"prototype-secret").decode("ascii")
    return {
        "peer_id": peer_id,
        "key_id": "key-1",
        "signing_secret_b64": secret,
    }


async def test_p2p_pair_and_signed_import_export_cycle() -> None:
    application, settings = _build_app()
    now = datetime.now(UTC).replace(microsecond=0)
    secret = base64.b64encode(b"prototype-secret").decode("ascii")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        pair_response = await client.post(
            "/v1/p2p/pair",
            headers=_AUTH_HEADERS,
            json=_pair_request(peer_id=settings.owner_id),
        )
        assert pair_response.status_code == 200
        assert pair_response.json() == {
            "owner_id": settings.owner_id,
            "peer_id": settings.owner_id,
            "key_id": "key-1",
            "paired": True,
        }

        export_response = await client.post(
            "/v1/p2p/envelope/export",
            headers=_AUTH_HEADERS,
            json={
                "envelope_id": "env-1",
                "sender_peer_id": settings.owner_id,
                "recipient_peer_id": settings.owner_id,
                "sender_key_id": "key-1",
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "nonce": "nonce-1",
                "payload": {"message": "hello"},
                "schema_version": "knowledge.v1",
                "sensitivity": "private",
                "retention_class": "keep",
                "trust": "verified",
                "source_time": now.isoformat(),
                "signing_secret_b64": secret,
            },
        )
        assert export_response.status_code == 200
        exported = export_response.json()
        assert exported["envelope_id"] == "env-1"
        assert exported["payload_sha256"]
        assert exported["signature"]

        import_response = await client.post(
            "/v1/p2p/envelope/import",
            headers=_AUTH_HEADERS,
            json={
                "envelope_id": "env-1",
                "sender_peer_id": settings.owner_id,
                "recipient_peer_id": settings.owner_id,
                "owner_id": settings.owner_id,
                "sender_key_id": "key-1",
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "nonce": "nonce-1",
                "payload": {"message": "hello"},
                "schema_version": "knowledge.v1",
                "sensitivity": "private",
                "retention_class": "keep",
                "trust": "verified",
                "source_time": now.isoformat(),
                "signature": exported["signature"],
            },
        )
        assert import_response.status_code == 200
        imported = import_response.json()
        assert imported["created"] is True
        assert imported["envelope_sha256"] == exported["payload_sha256"]
        assert imported["quarantine_item_count"] == 1

        replay_response = await client.post(
            "/v1/p2p/envelope/import",
            headers=_AUTH_HEADERS,
            json={
                "envelope_id": "env-1",
                "sender_peer_id": settings.owner_id,
                "recipient_peer_id": settings.owner_id,
                "owner_id": settings.owner_id,
                "sender_key_id": "key-1",
                "issued_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=10)).isoformat(),
                "nonce": "nonce-1",
                "payload": {"message": "hello"},
                "schema_version": "knowledge.v1",
                "sensitivity": "private",
                "retention_class": "keep",
                "trust": "verified",
                "source_time": now.isoformat(),
                "signature": exported["signature"],
            },
        )
        assert replay_response.status_code == 422

        status_response = await client.get("/v1/p2p/status", headers=_AUTH_HEADERS)
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["paired_peers"] == 1
        assert status_payload["paired_keys"] == 1
        assert status_payload["quarantine_item_count"] == 1
