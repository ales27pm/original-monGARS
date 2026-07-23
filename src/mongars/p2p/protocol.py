from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Final, Mapping

Clock = Callable[[], datetime]

SUPPORTED_PROTOCOL_VERSIONS: Final = (1,)
SUPPORTED_SCHEMA_VERSIONS: Final = ("knowledge.v1",)
ALLOWED_SENSITIVITY: Final = ("private", "shared", "restricted", "public")
ALLOWED_RETENTION_CLASS: Final = ("keep", "ttl_30d", "ttl_90d", "legal_hold")
ALLOWED_TRUST: Final = ("unknown", "unverified", "verified")
MAX_PAYLOAD_BYTES: Final = 1_000_000


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def _digest_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_timestamp(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalize_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return normalized


def _encode_payload(payload: bytes) -> str:
    return base64.b64encode(payload).decode("ascii")


def _decode_payload(payload_bytes: str) -> bytes:
    try:
        return base64.b64decode(payload_bytes, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("payload_bytes must be valid base64") from exc


class P2PValidationError(ValueError):
    pass


class P2PProtocolError(P2PValidationError):
    pass


class P2POwnerMismatch(P2PValidationError):
    pass


class P2PRecipientMismatch(P2PValidationError):
    pass


class P2PReplayError(P2PValidationError):
    pass


class P2PKeyMissing(P2PValidationError):
    pass


class P2PKeyRevoked(P2PValidationError):
    pass


class P2PExpiredEnvelope(P2PValidationError):
    pass


class P2PEnvelopeIntegrityError(P2PValidationError):
    pass


class P2PAuditAction:
    PAIR: Final = "pair"
    REVOKE_KEY: Final = "revoke_key"
    REVOKE_PEER: Final = "revoke_peer"


@dataclass(frozen=True, slots=True)
class P2PEnvelopeMetadata:
    schema_version: str
    sensitivity: str
    retention_class: str
    trust: str
    source_time: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "schema_version",
            _normalize_text(self.schema_version, "schema_version"),
        )
        object.__setattr__(
            self,
            "sensitivity",
            _normalize_text(self.sensitivity, "sensitivity"),
        )
        object.__setattr__(
            self,
            "retention_class",
            _normalize_text(self.retention_class, "retention_class"),
        )
        object.__setattr__(self, "trust", _normalize_text(self.trust, "trust"))
        if self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError("unsupported schema version")
        if self.sensitivity not in ALLOWED_SENSITIVITY:
            raise ValueError("unsupported sensitivity")
        if self.retention_class not in ALLOWED_RETENTION_CLASS:
            raise ValueError("unsupported retention class")
        if self.trust not in ALLOWED_TRUST:
            raise ValueError("unsupported trust label")
        object.__setattr__(
            self,
            "source_time",
            _normalize_timestamp(self.source_time, "source_time"),
        )


@dataclass(frozen=True, slots=True)
class P2PEnvelope:
    protocol_version: int
    envelope_id: str
    sender_peer_id: str
    recipient_peer_id: str
    owner_id: str
    sender_key_id: str
    issued_at: datetime
    expires_at: datetime
    nonce: str
    metadata: P2PEnvelopeMetadata
    payload_bytes: str
    payload_sha256: str
    signature: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "envelope_id",
            _normalize_text(self.envelope_id, "envelope_id"),
        )
        object.__setattr__(
            self,
            "sender_peer_id",
            _normalize_text(self.sender_peer_id, "sender_peer_id"),
        )
        object.__setattr__(
            self,
            "recipient_peer_id",
            _normalize_text(self.recipient_peer_id, "recipient_peer_id"),
        )
        object.__setattr__(self, "owner_id", _normalize_text(self.owner_id, "owner_id"))
        object.__setattr__(
            self,
            "sender_key_id",
            _normalize_text(self.sender_key_id, "sender_key_id"),
        )
        object.__setattr__(self, "nonce", _normalize_text(self.nonce, "nonce"))

        if self.protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError("unsupported protocol version")
        issued_at = _normalize_timestamp(self.issued_at, "issued_at")
        expires_at = _normalize_timestamp(self.expires_at, "expires_at")
        if issued_at > expires_at:
            raise ValueError("issued_at must not be after expires_at")
        object.__setattr__(self, "issued_at", issued_at)
        object.__setattr__(self, "expires_at", expires_at)

        payload = _decode_payload(self.payload_bytes)
        if len(payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("payload_bytes exceeds the protocol cap")
        object.__setattr__(self, "payload_bytes", _encode_payload(payload))
        if _digest_hex(payload) != self.payload_sha256:
            raise ValueError("payload_sha256 does not match payload bytes")
        signature = self.signature.lower()
        if len(signature) != 64 or not all(
            ch in "0123456789abcdef" for ch in signature
        ):
            raise ValueError("signature must be a lowercase SHA-256 hex value")
        object.__setattr__(self, "signature", signature)

    @property
    def payload(self) -> bytes:
        return _decode_payload(self.payload_bytes)

    @classmethod
    def build(
        cls,
        *,
        protocol_version: int,
        envelope_id: str,
        sender_peer_id: str,
        recipient_peer_id: str,
        owner_id: str,
        sender_key_id: str,
        issued_at: datetime,
        expires_at: datetime,
        nonce: str,
        metadata: P2PEnvelopeMetadata,
        payload: Mapping[str, Any],
        signing_key: bytes,
    ) -> "P2PEnvelope":
        payload_bytes = _canonical_bytes(payload)
        payload_sha256 = _digest_hex(payload_bytes)
        payload_b64 = _encode_payload(payload_bytes)
        envelope = cls(
            protocol_version=protocol_version,
            envelope_id=envelope_id,
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            owner_id=owner_id,
            sender_key_id=sender_key_id,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            metadata=metadata,
            payload_bytes=payload_b64,
            payload_sha256=payload_sha256,
            signature="0" * 64,
        )
        signature = _hmac_signature(signing_key, envelope._signing_payload())
        envelope = cls(
            protocol_version=protocol_version,
            envelope_id=envelope_id,
            sender_peer_id=sender_peer_id,
            recipient_peer_id=recipient_peer_id,
            owner_id=owner_id,
            sender_key_id=sender_key_id,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            metadata=metadata,
            payload_bytes=payload_b64,
            payload_sha256=payload_sha256,
            signature=signature,
        )
        return envelope

    def _signing_payload(self) -> bytes:
        return _canonical_bytes(
            {
                "protocol_version": self.protocol_version,
                "envelope_id": self.envelope_id,
                "sender_peer_id": self.sender_peer_id,
                "recipient_peer_id": self.recipient_peer_id,
                "owner_id": self.owner_id,
                "sender_key_id": self.sender_key_id,
                "issued_at": self.issued_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "nonce": self.nonce,
                "schema_version": self.metadata.schema_version,
                "sensitivity": self.metadata.sensitivity,
                "retention_class": self.metadata.retention_class,
                "trust": self.metadata.trust,
                "source_time": self.metadata.source_time.isoformat(),
                "payload_bytes": self.payload_bytes,
                "payload_sha256": self.payload_sha256,
            }
        )

    def verify_signature(self, *, signing_key: bytes) -> None:
        expected = _hmac_signature(signing_key, self._signing_payload())
        if not hmac.compare_digest(expected, self.signature):
            raise P2PEnvelopeIntegrityError("signature does not match envelope payload")
        computed = _digest_hex(self.payload)
        if not hmac.compare_digest(computed, self.payload_sha256):
            raise P2PEnvelopeIntegrityError("payload hash mismatch")

    @property
    def provenance(self) -> dict[str, str | int]:
        return {
            "source_peer_id": self.sender_peer_id,
            "owner_id": self.owner_id,
            "sender_key_id": self.sender_key_id,
            "protocol_version": self.protocol_version,
            "schema_version": self.metadata.schema_version,
            "sensitivity": self.metadata.sensitivity,
            "retention_class": self.metadata.retention_class,
            "trust": self.metadata.trust,
            "issued_at": self.issued_at.isoformat(),
            "source_time": self.metadata.source_time.isoformat(),
        }


def _hmac_signature(secret: bytes, message: bytes) -> str:
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class P2PPairingEvent:
    at: datetime
    actor: str
    peer_id: str
    action: str
    key_id: str
    reason: str | None = None


class P2PPairingRegistry:
    """Local pairing and key lifecycle ledger for explicit peer trust decisions."""

    def __init__(self, *, owner_id: str, now: Clock) -> None:
        self._owner_id = _normalize_text(owner_id, "owner_id")
        self._now = now
        self._peers: dict[str, dict[str, bytes]] = {}
        self._revoked: set[tuple[str, str]] = set()
        self._events: list[P2PPairingEvent] = []

    def pair_peer(
        self,
        *,
        actor: str,
        peer_id: str,
        key_id: str,
        secret: bytes,
    ) -> None:
        peer = _normalize_text(peer_id, "peer_id")
        key = _normalize_text(key_id, "key_id")
        actor = _normalize_text(actor, "actor")
        if not secret:
            raise ValueError("shared secret must not be empty")
        self._peers.setdefault(peer, {})
        self._peers[peer][key] = secret
        self._revoked.discard((peer, key))
        self._events.append(
            P2PPairingEvent(
                at=self._now(),
                actor=actor,
                peer_id=peer,
                action=P2PAuditAction.PAIR,
                key_id=key,
            )
        )

    def revoke_peer_key(
        self,
        *,
        actor: str,
        peer_id: str,
        key_id: str,
        reason: str | None = None,
    ) -> None:
        peer = _normalize_text(peer_id, "peer_id")
        key = _normalize_text(key_id, "key_id")
        actor = _normalize_text(actor, "actor")
        if peer not in self._peers or key not in self._peers[peer]:
            raise P2PKeyMissing("unknown peer key")
        self._revoked.add((peer, key))
        self._events.append(
            P2PPairingEvent(
                at=self._now(),
                actor=actor,
                peer_id=peer,
                action=P2PAuditAction.REVOKE_KEY,
                key_id=key,
                reason=reason,
            )
        )

    def revoke_peer(self, *, actor: str, peer_id: str, reason: str | None = None) -> None:
        peer = _normalize_text(peer_id, "peer_id")
        actor = _normalize_text(actor, "actor")
        if peer not in self._peers:
            raise P2PKeyMissing("unknown peer")
        for key_id in tuple(self._peers[peer].keys()):
            self._revoked.add((peer, key_id))
            self._events.append(
                P2PPairingEvent(
                    at=self._now(),
                    actor=actor,
                    peer_id=peer,
                    action=P2PAuditAction.REVOKE_PEER,
                    key_id=key_id,
                    reason=reason,
                )
            )

    def resolve_key(self, peer_id: str, key_id: str) -> bytes | None:
        peer = _normalize_text(peer_id, "peer_id")
        key = _normalize_text(key_id, "key_id")
        if (peer, key) in self._revoked:
            return None
        return self._peers.get(peer, {}).get(key)

    def is_key_revoked(self, *, peer_id: str, key_id: str) -> bool:
        peer = _normalize_text(peer_id, "peer_id")
        key = _normalize_text(key_id, "key_id")
        return (peer, key) in self._revoked

    @property
    def events(self) -> tuple[P2PPairingEvent, ...]:
        return tuple(self._events)


@dataclass(frozen=True, slots=True)
class P2PEnvelopeValidationResult:
    envelope: P2PEnvelope
    validated_at: datetime


@dataclass
class P2PReplayCache:
    ttl_seconds: int
    seen: dict[tuple[str, str], datetime] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")

    def check(self, *, now: datetime, sender_peer_id: str, nonce: str) -> None:
        self._sweep_expired(now=now)
        key = (sender_peer_id, nonce)
        if key in self.seen:
            raise P2PReplayError("replay nonce detected")
        self.seen[key] = now

    def _sweep_expired(self, *, now: datetime) -> None:
        stale = [
            key
            for key, seen_at in self.seen.items()
            if (now - seen_at).total_seconds() > self.ttl_seconds
        ]
        for key in stale:
            del self.seen[key]


def validate_p2p_envelope(
    *,
    envelope: P2PEnvelope,
    expected_recipient: str,
    expected_owner: str,
    pairing_registry: P2PPairingRegistry,
    replay_cache: P2PReplayCache,
    now: datetime,
) -> P2PEnvelopeValidationResult:
    if envelope.recipient_peer_id != _normalize_text(expected_recipient, "expected_recipient"):
        raise P2PRecipientMismatch("envelope recipient does not match this node")
    if envelope.owner_id != _normalize_text(expected_owner, "expected_owner"):
        raise P2POwnerMismatch("envelope owner does not match local owner")
    if now < envelope.issued_at:
        raise P2PValidationError("envelope not valid before issuance time")
    if now > envelope.expires_at:
        raise P2PExpiredEnvelope("envelope has expired")
    signing_key = pairing_registry.resolve_key(
        peer_id=envelope.sender_peer_id,
        key_id=envelope.sender_key_id,
    )
    if signing_key is None:
        if pairing_registry.is_key_revoked(
            peer_id=envelope.sender_peer_id,
            key_id=envelope.sender_key_id,
        ):
            raise P2PKeyRevoked("sender key has been revoked")
        raise P2PKeyMissing("no active key for sender")
    envelope.verify_signature(signing_key=signing_key)
    replay_cache.check(
        now=now,
        sender_peer_id=envelope.sender_peer_id,
        nonce=envelope.nonce,
    )
    return P2PEnvelopeValidationResult(envelope=envelope, validated_at=now)


@dataclass(frozen=True, slots=True)
class P2PQuarantineRecord:
    envelope_id: str
    envelope_sha256: str
    recorded_at: datetime
    envelope: P2PEnvelope
    provenance: dict[str, str | int]
    payload_bytes: int
    reviewed: bool = False


@dataclass
class P2PQuarantineStore:
    max_items: int
    max_bytes: int
    retention_ttl_seconds: int
    now: Clock
    _items: dict[tuple[str, str], P2PQuarantineRecord] = field(default_factory=dict, init=False)
    _order: deque[tuple[str, str]] = field(default_factory=deque, init=False)
    _used_bytes: int = field(default=0, init=False)
    _history: dict[tuple[str, str], P2PQuarantineRecord] = field(
        default_factory=dict,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.max_items < 1:
            raise ValueError("max_items must be positive")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if self.retention_ttl_seconds < 1:
            raise ValueError("retention_ttl_seconds must be positive")
        self._items = {}
        self._order = deque()
        self._used_bytes = 0

    @property
    def item_count(self) -> int:
        return len(self._items)

    @property
    def used_bytes(self) -> int:
        return self._used_bytes

    def add(
        self,
        *,
        envelope: P2PEnvelope,
        reviewed: bool = False,
    ) -> tuple[P2PQuarantineRecord, bool]:
        key = (envelope.envelope_id, envelope.payload_sha256)
        if key in self._history:
            record = self._history[key]
            if key in self._items:
                return record, False
            while (
                len(self._items) >= self.max_items
                or self._used_bytes + record.payload_bytes > self.max_bytes
            ):
                self._evict_oldest()
            self._items[key] = record
            self._order.append(key)
            self._used_bytes += record.payload_bytes
            return record, False
        bytes_required = len(envelope.payload) + 192
        if bytes_required > self.max_bytes:
            raise ValueError("payload exceeds quarantine capacity")
        while (
            len(self._items) >= self.max_items
            or self._used_bytes + bytes_required > self.max_bytes
        ):
            self._evict_oldest()
        record = P2PQuarantineRecord(
            envelope_id=envelope.envelope_id,
            envelope_sha256=envelope.payload_sha256,
            recorded_at=self.now(),
            envelope=envelope,
            reviewed=reviewed,
            payload_bytes=bytes_required,
            provenance=envelope.provenance,
        )
        self._items[key] = record
        self._order.append(key)
        self._used_bytes += bytes_required
        self._history[key] = record
        return record, True

    def delete(self, *, envelope_id: str, payload_sha256: str) -> bool:
        key = (_normalize_text(envelope_id, "envelope_id"), payload_sha256)
        record = self._items.pop(key, None)
        if record is None:
            return False
        self._order = deque(item for item in self._order if item != key)
        self._used_bytes -= record.payload_bytes
        return True

    def remove_expired(self) -> list[tuple[str, str]]:
        now = self.now()
        expired = [
            key
            for key, item in self._items.items()
            if (now - item.recorded_at).total_seconds() > self.retention_ttl_seconds
        ]
        removed: list[tuple[str, str]] = []
        for key in expired:
            self.delete(envelope_id=key[0], payload_sha256=key[1])
            removed.append(key)
        return removed

    def _evict_oldest(self) -> None:
        if not self._order:
            raise ValueError("cannot evict quarantine items: store is already empty")
        key = self._order.popleft()
        record = self._items.pop(key)
        self._used_bytes -= record.payload_bytes
