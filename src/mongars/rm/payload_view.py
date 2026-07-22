from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PAYLOAD_PAGE_CHARACTERS = 8_000
PAYLOAD_PREVIEW_EDGE_CHARACTERS = 1_500
PAYLOAD_FORMAT = "sorted-pretty-json-v1"


def serialize_task_payload(payload: dict[str, Any]) -> str:
    """Render a stable, human-reviewable representation of a validated task payload."""

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


@dataclass(frozen=True, slots=True)
class TaskPayloadSummary:
    byte_length: int
    character_count: int
    page_count: int
    page_size_characters: int
    top_level_field_count: int
    preview_head: str
    preview_tail: str
    preview_omitted_characters: int


@dataclass(frozen=True, slots=True)
class TaskPayloadPage:
    page_index: int
    page_count: int
    page_size_characters: int
    character_start: int
    character_end: int
    content: str


def summarize_task_payload(payload: dict[str, Any]) -> TaskPayloadSummary:
    rendered = serialize_task_payload(payload)
    character_count = len(rendered)
    page_count = max(1, (character_count + PAYLOAD_PAGE_CHARACTERS - 1) // PAYLOAD_PAGE_CHARACTERS)
    preview_limit = PAYLOAD_PREVIEW_EDGE_CHARACTERS * 2
    if character_count <= preview_limit:
        preview_head = rendered
        preview_tail = ""
        omitted = 0
    else:
        preview_head = rendered[:PAYLOAD_PREVIEW_EDGE_CHARACTERS]
        preview_tail = rendered[-PAYLOAD_PREVIEW_EDGE_CHARACTERS:]
        omitted = character_count - preview_limit

    return TaskPayloadSummary(
        byte_length=len(rendered.encode("utf-8")),
        character_count=character_count,
        page_count=page_count,
        page_size_characters=PAYLOAD_PAGE_CHARACTERS,
        top_level_field_count=len(payload),
        preview_head=preview_head,
        preview_tail=preview_tail,
        preview_omitted_characters=omitted,
    )


def task_payload_page(payload: dict[str, Any], *, page_index: int) -> TaskPayloadPage:
    rendered = serialize_task_payload(payload)
    character_count = len(rendered)
    page_count = max(1, (character_count + PAYLOAD_PAGE_CHARACTERS - 1) // PAYLOAD_PAGE_CHARACTERS)
    if page_index < 0 or page_index >= page_count:
        raise IndexError("payload page is out of range")

    start = page_index * PAYLOAD_PAGE_CHARACTERS
    end = min(character_count, start + PAYLOAD_PAGE_CHARACTERS)
    return TaskPayloadPage(
        page_index=page_index,
        page_count=page_count,
        page_size_characters=PAYLOAD_PAGE_CHARACTERS,
        character_start=start,
        character_end=end,
        content=rendered[start:end],
    )
