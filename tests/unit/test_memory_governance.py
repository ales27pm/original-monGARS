from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mongars.db.models import MemoryDocument
from mongars.memory.repository import (
    MemoryGovernanceConflict,
    validate_duplicate_governance,
)


@pytest.mark.parametrize(
    ("existing", "requested"),
    [
        (existing, requested)
        for existing in ("private", "shared", "restricted")
        for requested in ("private", "shared", "restricted")
        if existing != requested
    ],
)
def test_every_sensitivity_transition_requires_explicit_governance_resolution(
    existing: str,
    requested: str,
) -> None:
    document = _document(sensitivity=existing, retention_class="keep")

    with pytest.raises(MemoryGovernanceConflict, match="sensitivity"):
        validate_duplicate_governance(
            document,
            sensitivity=requested,
            retention_class="keep",
        )


@pytest.mark.parametrize(
    ("existing", "requested"),
    [
        (existing, requested)
        for existing in ("keep", "ttl_30d", "ttl_90d", "legal_hold")
        for requested in ("keep", "ttl_30d", "ttl_90d", "legal_hold")
        if existing != requested
    ],
)
def test_every_retention_transition_requires_explicit_governance_resolution(
    existing: str,
    requested: str,
) -> None:
    document = _document(sensitivity="private", retention_class=existing)

    with pytest.raises(MemoryGovernanceConflict, match="retention_class"):
        validate_duplicate_governance(
            document,
            sensitivity="private",
            retention_class=requested,
        )


def test_equal_governance_accepts_duplicate_without_extending_ttl() -> None:
    original_expiry = datetime.now(UTC) + timedelta(days=30)
    document = _document(
        sensitivity="restricted",
        retention_class="ttl_30d",
        expires_at=original_expiry,
    )

    validate_duplicate_governance(
        document,
        sensitivity="restricted",
        retention_class="ttl_30d",
    )

    assert document.expires_at == original_expiry


def _document(
    *,
    sensitivity: str,
    retention_class: str,
    expires_at: datetime | None = None,
) -> MemoryDocument:
    return MemoryDocument(
        owner_id="owner",
        source_type="note",
        source_uri=None,
        source_sha256=b"x" * 32,
        title=None,
        mime_type="text/plain",
        sensitivity=sensitivity,
        retention_class=retention_class,
        expires_at=expires_at,
        metadata_json={},
    )
