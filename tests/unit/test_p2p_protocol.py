from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from mongars.p2p import (
    P2PEnvelope,
    P2PEnvelopeIntegrityError,
    P2PEnvelopeMetadata,
    P2PExpiredEnvelope,
    P2PKeyRevoked,
    P2POwnerMismatch,
    P2PQuarantineStore,
    P2PReplayCache,
    P2PRecipientMismatch,
    P2PPairingRegistry,
    P2PReplayError,
    validate_p2p_envelope,
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.current = value

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def _metadata(*, issue_time: datetime) -> P2PEnvelopeMetadata:
    return P2PEnvelopeMetadata(
        schema_version="knowledge.v1",
        sensitivity="private",
        retention_class="keep",
        trust="verified",
        source_time=issue_time,
    )


def _new_registry(clock: _Clock) -> P2PPairingRegistry:
    registry = P2PPairingRegistry(owner_id="local-owner", now=clock)
    registry.pair_peer(
        actor="local-operator",
        peer_id="peer-a",
        key_id="key-a-1",
        secret=b"secret-key-peer-a-v1",
    )
    return registry


def _build_envelope(
    *,
    envelope_id: str,
    recipient: str = "local-peer",
    owner: str = "local-owner",
    nonce: str,
    key: bytes,
    issue_time: datetime,
    text: str = "hello from peer",
    expires_at: datetime | None = None,
) -> P2PEnvelope:
    return P2PEnvelope.build(
        protocol_version=1,
        envelope_id=envelope_id,
        sender_peer_id="peer-a",
        recipient_peer_id=recipient,
        owner_id=owner,
        sender_key_id="key-a-1",
        issued_at=issue_time,
        expires_at=expires_at if expires_at is not None else issue_time + timedelta(minutes=10),
        nonce=nonce,
        metadata=_metadata(issue_time=issue_time),
        payload={"kind": "knowledge.note", "text": text},
        signing_key=key,
    )


def test_valid_envelope_is_accepted_and_replay_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-valid",
        nonce="nonce-1",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
    )
    first = validate_p2p_envelope(
        envelope=envelope,
        expected_recipient="local-peer",
        expected_owner="local-owner",
        pairing_registry=registry,
        replay_cache=replay_cache,
        now=clock.current,
    )
    with pytest.raises(P2PReplayError, match="replay nonce detected"):
        validate_p2p_envelope(
            envelope=first.envelope,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
        )


def test_altered_signature_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-altered",
        nonce="nonce-alt",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
    )
    tampered = replace(envelope, signature="0" * 64)

    with pytest.raises(P2PEnvelopeIntegrityError, match="signature does not match"):
        validate_p2p_envelope(
            envelope=tampered,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
        )


def test_expired_envelope_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 5, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-expired",
        nonce="nonce-expired",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current - timedelta(minutes=20),
        expires_at=clock.current - timedelta(minutes=1),
    )
    with pytest.raises(P2PExpiredEnvelope, match="expired"):
        validate_p2p_envelope(
            envelope=envelope,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
    )


def test_wrong_recipient_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-recipient",
        nonce="nonce-recipient",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
        recipient="different-peer",
    )

    with pytest.raises(P2PRecipientMismatch, match="recipient does not match"):
        validate_p2p_envelope(
            envelope=envelope,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
        )


def test_revoked_sender_key_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-revoked",
        nonce="nonce-revoke",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
    )
    registry.revoke_peer_key(
        actor="local-operator",
        peer_id="peer-a",
        key_id="key-a-1",
        reason="device lost",
    )
    with pytest.raises(P2PKeyRevoked, match="revoked"):
        validate_p2p_envelope(
            envelope=envelope,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
        )


def test_wrong_owner_is_rejected() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    replay_cache = P2PReplayCache(ttl_seconds=3600)
    envelope = _build_envelope(
        envelope_id="env-owner",
        nonce="nonce-owner",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
        owner="other-owner",
    )

    with pytest.raises(P2POwnerMismatch, match="owner does not match"):
        validate_p2p_envelope(
            envelope=envelope,
            expected_recipient="local-peer",
            expected_owner="local-owner",
            pairing_registry=registry,
            replay_cache=replay_cache,
            now=clock.current,
        )


def test_quarantine_store_is_bounded_idempotent_and_records_provenance() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    store = P2PQuarantineStore(
        max_items=2,
        max_bytes=5000,
        retention_ttl_seconds=600,
        now=clock,
    )
    envelope_a = _build_envelope(
        envelope_id="env-a",
        nonce="nonce-a",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
        text="first",
    )
    envelope_b = _build_envelope(
        envelope_id="env-b",
        nonce="nonce-b",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
        text="second",
    )
    envelope_c = _build_envelope(
        envelope_id="env-c",
        nonce="nonce-c",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
        text="third",
    )

    first, created_first = store.add(envelope=envelope_a)
    second, created_second = store.add(envelope=envelope_b)
    _, created_third = store.add(envelope=envelope_c)
    assert created_first
    assert created_second
    assert created_third
    assert store.item_count == 2
    assert not store.delete(
        envelope_id=envelope_a.envelope_id,
        payload_sha256=envelope_a.payload_sha256,
    )
    assert store.delete(
        envelope_id=envelope_b.envelope_id,
        payload_sha256=envelope_b.payload_sha256,
    )
    assert store.delete(
        envelope_id=envelope_b.envelope_id,
        payload_sha256=envelope_b.payload_sha256,
    ) is False
    assert store.item_count == 1
    assert second.provenance["retention_class"] == "keep"

    duplicated, duplicate_created = store.add(envelope=envelope_b)
    assert not duplicate_created
    assert duplicated == store._items[(envelope_b.envelope_id, envelope_b.payload_sha256)]


def test_quarantine_retention_deletes_stale_items() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    store = P2PQuarantineStore(
        max_items=10,
        max_bytes=5000,
        retention_ttl_seconds=300,
        now=clock,
    )
    envelope = _build_envelope(
        envelope_id="env-retention",
        nonce="nonce-retention",
        key=b"secret-key-peer-a-v1",
        issue_time=clock.current,
    )
    store.add(envelope=envelope)
    assert store.item_count == 1

    clock.advance(timedelta(seconds=301))
    removed = store.remove_expired()

    assert removed == [(envelope.envelope_id, envelope.payload_sha256)]
    assert store.item_count == 0


def test_pairing_actions_emit_audit_events() -> None:
    clock = _Clock(datetime(2026, 7, 23, 12, 0, tzinfo=UTC))
    registry = _new_registry(clock)
    registry.pair_peer(
        actor="local-operator",
        peer_id="peer-b",
        key_id="key-b-1",
        secret=b"key-b",
    )
    registry.revoke_peer(
        actor="local-operator",
        peer_id="peer-b",
        reason="rotation",
    )

    actions = [event.action for event in registry.events]
    assert actions == ["pair", "pair", "revoke_peer"]
    assert registry.events[-1].reason == "rotation"
