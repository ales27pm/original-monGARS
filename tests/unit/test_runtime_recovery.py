from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mongars.api.routes.web import WEB_STATIC_ROOT, create_web_router


def _application(static_root: Path) -> FastAPI:
    application = FastAPI()
    application.include_router(create_web_router(static_root=static_root))
    return application


@pytest.mark.asyncio
async def test_checked_in_script_bundles_runtime_recovery_without_exposing_source() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(WEB_STATIC_ROOT)),
        base_url="http://testserver",
    ) as client:
        script = await client.get("/assets/app.js")
        recovery = await client.get("/assets/runtime-recovery.js")

    assert script.status_code == 200
    assert recovery.status_code == 404
    assert 'request("/v1/readyz", { allowUnavailable: true })' in script.text
    assert 'request("/v1/memory/reindex"' in script.text
    assert 'candidate?.kind === "memory.reindex"' in script.text
    assert 'document.getElementById("memory-reindex-recovery")' in script.text
    assert "state.defaultSummaryText" in script.text
    assert "state.refreshing" in script.text
    assert ".innerHTML" not in (WEB_STATIC_ROOT / "runtime-recovery.js").read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery_content", [None, b"\xff"])
async def test_app_script_falls_back_when_optional_recovery_source_is_unusable(
    tmp_path: Path,
    recovery_content: bytes | None,
) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (static_root / "app.js").write_text("console.log('base');", encoding="utf-8")
    (static_root / "app.css").write_text("body {}", encoding="utf-8")
    if recovery_content is not None:
        (static_root / "runtime-recovery.js").write_bytes(recovery_content)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(static_root)),
        base_url="http://testserver",
    ) as client:
        script = await client.get("/assets/app.js")

    assert script.status_code == 200
    assert script.text == "console.log('base');"


@pytest.mark.asyncio
async def test_recovery_bundle_is_cached_after_first_successful_read(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (static_root / "app.js").write_text("console.log('base');", encoding="utf-8")
    (static_root / "app.css").write_text("body {}", encoding="utf-8")
    recovery = static_root / "runtime-recovery.js"
    recovery.write_text("console.log('recovery-v1');", encoding="utf-8")
    application = _application(static_root)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        first = await client.get("/assets/app.js")
        recovery.write_text("console.log('recovery-v2');", encoding="utf-8")
        second = await client.get("/assets/app.js")

    assert first.status_code == second.status_code == 200
    assert first.text == second.text
    assert "recovery-v1" in second.text
    assert "recovery-v2" not in second.text
