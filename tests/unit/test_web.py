from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mongars.api.routes.web import WEB_STATIC_ROOT, create_web_router


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>monGARS</title>", encoding="utf-8")
    (root / "app.css").write_text("body { color: black; }", encoding="utf-8")
    (root / "app.js").write_text("console.log('monGARS');", encoding="utf-8")
    return root


def _application(static_root: Path) -> FastAPI:
    application = FastAPI()
    application.include_router(create_web_router(static_root=static_root))
    return application


@pytest.mark.asyncio
async def test_checked_in_web_bundle_is_complete_and_served() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(WEB_STATIC_ROOT)),
        base_url="http://testserver",
    ) as client:
        index, stylesheet, script = await asyncio.gather(
            client.get("/"),
            client.get("/assets/app.css"),
            client.get("/assets/app.js"),
        )

    assert index.status_code == stylesheet.status_code == script.status_code == 200
    assert '<link rel="stylesheet" href="/assets/app.css">' in index.text
    assert '<script src="/assets/app.js" defer></script>' in index.text
    assert "Review protected action" in script.text
    assert "task-review" in stylesheet.text


@pytest.mark.asyncio
async def test_web_index_has_strict_browser_policy_and_is_not_documented(
    static_root: Path,
) -> None:
    application = _application(static_root)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/")
        openapi = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.text == "<!doctype html><title>monGARS</title>"
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]
    assert response.headers["permissions-policy"] == "camera=(), geolocation=(), microphone=()"
    assert "/" not in openapi.json()["paths"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("asset_name", "expected_type", "expected_body"),
    [
        ("app.css", "text/css; charset=utf-8", "body { color: black; }"),
        ("app.js", "text/javascript; charset=utf-8", "console.log('monGARS');"),
    ],
)
async def test_web_assets_have_explicit_types_and_no_store_cache_policy(
    static_root: Path,
    asset_name: str,
    expected_type: str,
    expected_body: str,
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(static_root)),
        base_url="http://testserver",
    ) as client:
        response = await client.get(f"/assets/{asset_name}")

    assert response.status_code == 200
    assert response.text == expected_body
    assert response.headers["content-type"] == expected_type
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "untrusted_path",
    [
        "/assets/unknown.js",
        "/assets/%2e%2e/secret.txt",
        "/assets/subdirectory/app.js",
        "/assets/app.js.map",
    ],
)
async def test_web_assets_reject_every_path_outside_the_allowlist(
    static_root: Path,
    untrusted_path: str,
) -> None:
    (static_root.parent / "secret.txt").write_text("secret", encoding="utf-8")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(static_root)),
        base_url="http://testserver",
    ) as client:
        response = await client.get(untrusted_path)

    assert response.status_code == 404
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_web_assets_reject_a_symlink_escape(static_root: Path) -> None:
    outside = static_root.parent / "outside.js"
    outside.write_text("secret", encoding="utf-8")
    (static_root / "app.js").unlink()
    (static_root / "app.js").symlink_to(outside)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(static_root)),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/assets/app.js")

    assert response.status_code == 404
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_missing_web_bundle_fails_without_exposing_a_path(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_application(missing_root)),
        base_url="http://testserver",
    ) as client:
        index = await client.get("/")
        asset = await client.get("/assets/app.js")

    assert index.status_code == 503
    assert index.json() == {"detail": "Web interface is unavailable"}
    assert str(missing_root) not in index.text
    assert asset.status_code == 404
