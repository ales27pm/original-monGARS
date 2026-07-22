from __future__ import annotations

import json

import pytest

from mongars.rm.payload_view import (
    PAYLOAD_PAGE_CHARACTERS,
    PAYLOAD_PREVIEW_EDGE_CHARACTERS,
    serialize_task_payload,
    summarize_task_payload,
    task_payload_page,
)


def test_payload_summary_is_bounded_and_reports_complete_sorted_pretty_json() -> None:
    payload = {"z": "😀" * 9_000, "a": "first"}

    rendered = serialize_task_payload(payload)
    summary = summarize_task_payload(payload)

    assert rendered == json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    assert rendered.index('"a"') < rendered.index('"z"')
    assert summary.character_count == len(rendered)
    assert summary.byte_length == len(rendered.encode("utf-8"))
    assert summary.top_level_field_count == 2
    assert len(summary.preview_head) == PAYLOAD_PREVIEW_EDGE_CHARACTERS
    assert len(summary.preview_tail) == PAYLOAD_PREVIEW_EDGE_CHARACTERS
    assert summary.preview_omitted_characters == len(rendered) - 3_000
    assert summary.page_count == (len(rendered) + PAYLOAD_PAGE_CHARACTERS - 1) // 8_000


def test_payload_pages_are_bounded_unicode_safe_and_reconstruct_exact_rendering() -> None:
    payload = {"message": "é😀" * 10_000}
    rendered = serialize_task_payload(payload)
    summary = summarize_task_payload(payload)

    pages = [task_payload_page(payload, page_index=index) for index in range(summary.page_count)]

    assert "".join(page.content for page in pages) == rendered
    assert all(len(page.content) <= PAYLOAD_PAGE_CHARACTERS for page in pages)
    assert [page.character_start for page in pages] == [
        index * PAYLOAD_PAGE_CHARACTERS for index in range(summary.page_count)
    ]
    assert pages[-1].character_end == len(rendered)
    assert "\ud83d" not in rendered
    assert "\ude00" not in rendered


def test_small_payload_preview_is_exact_and_out_of_range_page_is_rejected() -> None:
    payload = {"enabled": True, "message": "Bonjour, Laval"}
    rendered = serialize_task_payload(payload)
    summary = summarize_task_payload(payload)

    assert summary.preview_head == rendered
    assert summary.preview_tail == ""
    assert summary.preview_omitted_characters == 0
    assert task_payload_page(payload, page_index=0).content == rendered
    with pytest.raises(IndexError, match="out of range"):
        task_payload_page(payload, page_index=1)
