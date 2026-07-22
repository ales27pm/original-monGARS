from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mongars.rm.contracts import UnsupportedTaskKind, normalize_task_payload


def test_unknown_task_kind_is_rejected() -> None:
    with pytest.raises(UnsupportedTaskKind, match="unsupported task kind"):
        normalize_task_payload("shell.execute", {"command": "id"})


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("memory.search", {"query": "notes", "unexpected": True}),
        ("memory.note.create", {"text": "remember", "unexpected": True}),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
                "unexpected": True,
            },
        ),
    ],
)
def test_extra_payload_fields_are_rejected(
    kind: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        normalize_task_payload(kind, payload)

    assert exc_info.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("memory.search", {}),
        ("memory.search", {"query": ""}),
        ("memory.search", {"query": "notes", "top_k": 0}),
        ("memory.note.create", {}),
        ("memory.note.create", {"text": ""}),
        ("memory.note.create", {"text": "note", "sensitivity": "public"}),
        ("memory.note.create", {"text": "note", "retention_class": "forever"}),
        ("document.ingest", {}),
        (
            "document.ingest",
            {
                "staging_id": "not-a-uuid",
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "A" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "application/octet-stream",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 0,
                "source_timestamp": "2026-07-22T12:30:00Z",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
                "sensitivity": "public",
            },
        ),
        (
            "document.ingest",
            {
                "staging_id": str(uuid4()),
                "original_filename": "notes.txt",
                "source_sha256": "a" * 64,
                "detected_mime_type": "text/plain",
                "byte_size": 5,
                "source_timestamp": "2026-07-22T12:30:00Z",
                "retention_class": "forever",
            },
        ),
    ],
)
def test_invalid_payload_values_are_rejected(
    kind: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        normalize_task_payload(kind, payload)


def test_search_payload_is_normalized_with_defaults() -> None:
    source = {"query": "notes"}

    normalized = normalize_task_payload("memory.search", source)

    assert normalized == {"query": "notes", "top_k": 8}
    assert source == {"query": "notes"}


def test_note_payload_is_normalized_with_security_defaults() -> None:
    normalized = normalize_task_payload(
        "memory.note.create",
        {"text": "remember", "title": "Reminder"},
    )

    assert normalized == {
        "text": "remember",
        "title": "Reminder",
        "sensitivity": "private",
        "retention_class": "keep",
    }


def test_document_payload_is_normalized_with_governance_defaults() -> None:
    staging_id = uuid4()
    source_timestamp = datetime(2026, 7, 22, 8, 30, tzinfo=UTC)
    source = {
        "staging_id": str(staging_id),
        "original_filename": "notes.txt",
        "source_sha256": "a" * 64,
        "detected_mime_type": "text/plain",
        "byte_size": 5,
        "source_timestamp": source_timestamp.isoformat(),
        "title": "Reviewed upload",
    }

    normalized = normalize_task_payload("document.ingest", source)

    assert normalized == {
        "staging_id": str(staging_id),
        "original_filename": "notes.txt",
        "source_sha256": "a" * 64,
        "detected_mime_type": "text/plain",
        "byte_size": 5,
        "source_timestamp": "2026-07-22T08:30:00Z",
        "title": "Reviewed upload",
        "sensitivity": "private",
        "retention_class": "keep",
    }
    assert source["source_timestamp"] == source_timestamp.isoformat()
