from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status

from mongars.api.dependencies import PrincipalDependency, SettingsDependency
from mongars.api.schemas import (
    P2PEnvelopeExportRequest,
    P2PEnvelopeExportResponse,
    P2PEnvelopeImportRequest,
    P2PEnvelopeImportResponse,
    P2PStatusResponse,
    P2PPairRequest,
    P2PPairResponse,
)
from mongars.config import Settings
from mongars.p2p import (
    P2PEnvelope,
    P2PEnvelopeMetadata,
    P2PQuarantineStore,
    P2PPairingRegistry,
    P2PReplayCache,
    P2PValidationError,
    validate_p2p_envelope,
)

router = APIRouter(prefix="/v1/p2p", tags=["p2p"])


_P2P_MAX_QUARANTINE_ITEMS = 128
_P2P_MAX_QUARANTINE_BYTES = 1_000_000
_P2P_RETENTION_TTL_SECONDS = 600
_P2P_REPLAY_TTL_SECONDS = 3600


@dataclass
class _P2PRuntime:
    pairing_registry: P2PPairingRegistry
    replay_cache: P2PReplayCache
    quarantine: P2PQuarantineStore


def _p2p_now() -> datetime:
    return datetime.now(UTC)


def _canonical_payload_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _payload_sha256(payload_bytes: bytes) -> str:
    return hashlib.sha256(payload_bytes).hexdigest()


def _decode_secret(secret_b64: str) -> bytes:
    try:
        secret = base64.b64decode(secret_b64, validate=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("signing_secret_b64 must be valid base64") from exc
    if not secret:
        raise ValueError("signing secret must not be empty")
    return secret


def _get_runtime(request: Request, settings: Settings) -> _P2PRuntime:
    runtime = getattr(request.app.state, "p2p", None)
    if runtime is None:
        runtime = _P2PRuntime(
            pairing_registry=P2PPairingRegistry(owner_id=settings.owner_id, now=_p2p_now),
            replay_cache=P2PReplayCache(ttl_seconds=_P2P_REPLAY_TTL_SECONDS),
            quarantine=P2PQuarantineStore(
                max_items=_P2P_MAX_QUARANTINE_ITEMS,
                max_bytes=_P2P_MAX_QUARANTINE_BYTES,
                retention_ttl_seconds=_P2P_RETENTION_TTL_SECONDS,
                now=_p2p_now,
            ),
        )
        request.app.state.p2p = runtime
    if not isinstance(runtime, _P2PRuntime):
        raise RuntimeError("p2p runtime state is malformed")
    return runtime


def _envelope_metadata_from_request(
    request: P2PEnvelopeExportRequest | P2PEnvelopeImportRequest,
) -> P2PEnvelopeMetadata:
    return P2PEnvelopeMetadata(
        schema_version=request.schema_version,
        sensitivity=request.sensitivity,
        retention_class=request.retention_class,
        trust=request.trust,
        source_time=request.source_time,
    )


@router.post("/pair", response_model=P2PPairResponse)
async def create_pairing(
    request: P2PPairRequest,
    http_request: Request,
    principal: PrincipalDependency,
    settings: SettingsDependency,
) -> P2PPairResponse:
    runtime = _get_runtime(http_request, settings)
    try:
        runtime.pairing_registry.pair_peer(
            actor=principal.subject,
            peer_id=request.peer_id,
            key_id=request.key_id,
            secret=_decode_secret(request.signing_secret_b64),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return P2PPairResponse(
        owner_id=settings.owner_id,
        peer_id=request.peer_id,
        key_id=request.key_id,
        paired=True,
    )


@router.get("/status", response_model=P2PStatusResponse)
async def p2p_status(
    request: Request,
    settings: SettingsDependency,
) -> P2PStatusResponse:
    runtime = _get_runtime(request, settings)
    return P2PStatusResponse(
        owner_id=settings.owner_id,
        paired_peers=len(runtime.pairing_registry._peers),
        paired_keys=sum(len(keys) for keys in runtime.pairing_registry._peers.values()),
        quarantine_item_count=runtime.quarantine.item_count,
        quarantine_used_bytes=runtime.quarantine.used_bytes,
    )


@router.post("/envelope/export", response_model=P2PEnvelopeExportResponse)
async def create_signed_envelope(
    request: P2PEnvelopeExportRequest,
    http_request: Request,
    principal: PrincipalDependency,
    settings: SettingsDependency,
) -> P2PEnvelopeExportResponse:
    runtime = _get_runtime(http_request, settings)
    secret = _decode_secret(request.signing_secret_b64)
    try:
        envelope = P2PEnvelope.build(
            protocol_version=1,
            envelope_id=request.envelope_id,
            sender_peer_id=request.sender_peer_id,
            recipient_peer_id=request.recipient_peer_id,
            owner_id=principal.subject,
            sender_key_id=request.sender_key_id,
            issued_at=request.issued_at,
            expires_at=request.expires_at,
            nonce=request.nonce,
            metadata=_envelope_metadata_from_request(request),
            payload=request.payload,
            signing_key=secret,
        )
        runtime.pairing_registry.pair_peer(
            actor=principal.subject,
            peer_id=request.sender_peer_id,
            key_id=request.sender_key_id,
            secret=secret,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return P2PEnvelopeExportResponse(
        envelope_id=envelope.envelope_id,
        sender_peer_id=envelope.sender_peer_id,
        recipient_peer_id=envelope.recipient_peer_id,
        owner_id=envelope.owner_id,
        sender_key_id=envelope.sender_key_id,
        issued_at=envelope.issued_at,
        expires_at=envelope.expires_at,
        nonce=envelope.nonce,
        protocol_version=envelope.protocol_version,
        schema_version=envelope.metadata.schema_version,
        sensitivity=envelope.metadata.sensitivity,
        retention_class=envelope.metadata.retention_class,
        trust=envelope.metadata.trust,
        source_time=envelope.metadata.source_time,
        payload_sha256=envelope.payload_sha256,
        payload_bytes=envelope.payload_bytes,
        signature=envelope.signature,
    )


@router.post("/envelope/import", response_model=P2PEnvelopeImportResponse)
async def import_signed_envelope(
    request: P2PEnvelopeImportRequest,
    http_request: Request,
    principal: PrincipalDependency,
    settings: SettingsDependency,
) -> P2PEnvelopeImportResponse:
    payload_bytes = _canonical_payload_bytes(request.payload)
    envelope = P2PEnvelope(
        protocol_version=1,
        envelope_id=request.envelope_id,
        sender_peer_id=request.sender_peer_id,
        recipient_peer_id=request.recipient_peer_id,
        owner_id=request.owner_id,
        sender_key_id=request.sender_key_id,
        issued_at=request.issued_at,
        expires_at=request.expires_at,
        nonce=request.nonce,
        metadata=_envelope_metadata_from_request(request),
        payload_bytes=request.payload_bytes or base64.b64encode(payload_bytes).decode("ascii"),
        payload_sha256=_payload_sha256(payload_bytes),
        signature=request.signature,
    )
    runtime = _get_runtime(http_request, settings)
    try:
        validated = validate_p2p_envelope(
            envelope=envelope,
            expected_recipient=request.recipient_peer_id,
            expected_owner=principal.subject,
            pairing_registry=runtime.pairing_registry,
            replay_cache=runtime.replay_cache,
            now=_p2p_now(),
        )
        record, created = runtime.quarantine.add(
            envelope=validated.envelope,
            reviewed=False,
        )
    except P2PValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return P2PEnvelopeImportResponse(
        envelope_id=record.envelope_id,
        envelope_sha256=record.envelope_sha256,
        reviewed=record.reviewed,
        quarantine_item_count=runtime.quarantine.item_count,
        created=created,
    )


__all__ = ["router"]
