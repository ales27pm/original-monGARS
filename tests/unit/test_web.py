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


def test_web_approval_payload_review_is_bounded_paginated_and_stateful() -> None:
    script = (WEB_STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    stylesheet = (WEB_STATIC_ROOT / "app.css").read_text(encoding="utf-8")
    render_start = script.index("function renderTaskReview")
    render_end = script.index("function reconcileTaskReviews", render_start)
    render_source = script[render_start:render_end]

    assert "state.taskReviews.set(taskId, createTaskReview(detail))" in script
    assert "state.taskReviews.get(task.id)" in script
    assert "state.taskReviews.delete(taskId)" in script
    assert "const taskStatuses = new Map(state.tasks.map" in script
    assert 'if (taskStatuses.get(taskId) !== "waiting_approval")' in script

    assert "JSON.stringify" not in render_source
    assert "taskResultText(detail.payload)" not in script
    assert "reviewState.page.content" in render_source
    assert "taskPayloadPreview(payloadSummary)" in render_source
    assert 'element("code", "", reviewState.actionDigest)' in render_source
    assert "Open exact payload pages" in render_source
    assert "data-task-review-action" in script
    assert "/payload?page=${pageIndex}" in script
    assert "page.action_digest !== reviewState.actionDigest" in script
    assert "JSON.stringify(detail.payload" not in script
    assert "JSON.stringify({ action_digest: reviewState.actionDigest })" in script

    assert ".task-payload-page" in stylesheet
    assert ".task-payload-controls" in stylesheet
    assert ".task-digest code" in stylesheet
    assert "overflow-wrap: anywhere" in stylesheet


def test_web_document_upload_form_is_bounded_and_exposes_governance_controls() -> None:
    index = (WEB_STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert 'id="open-document"' in index
    assert 'id="document-dialog"' in index
    assert 'id="document-form"' in index
    assert 'id="document-file"' in index
    assert (
        'accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx,text/plain,text/markdown,'
        "text/html,application/pdf,application/vnd.openxmlformats-officedocument."
        'wordprocessingml.document"'
    ) in index
    assert 'id="document-title-input" type="text" maxlength="500"' in index
    assert 'id="document-sensitivity"' in index
    assert 'id="document-retention"' in index
    assert 'id="document-upload-result" aria-live="polite" hidden' in index
    assert 'id="document-upload-state">Waiting approval</dd>' in index
    assert 'id="document-task-link" href="#tasks"' in index
    assert "maximum 10 MB" in index


def test_web_document_upload_uses_authenticated_browser_multipart_and_safe_rendering() -> None:
    script = (WEB_STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    upload_start = script.index("async function uploadDocument")
    upload_end = script.index("function selectTaskFilter", upload_start)
    upload_source = script[upload_start:upload_end]
    response_start = script.index("function validateDocumentUploadResponse")
    response_end = script.index("async function uploadDocument", response_start)
    response_source = script[response_start:response_end]
    fetch_start = script.index("async function apiFetch")
    fetch_end = script.index("function toast", fetch_start)
    fetch_source = script[fetch_start:fetch_end]
    render_start = script.index("function renderSelectedDocument")
    render_end = script.index("function resetDocumentUpload", render_start)
    render_source = script[render_start:render_end]

    assert "const MAX_DOCUMENT_BYTES = 10_000_000" in script
    assert "file.size > MAX_DOCUMENT_BYTES" in script
    assert 'txt: "text/plain"' in script
    assert 'md: "text/markdown"' in script
    assert 'html: "text/html"' in script
    assert 'pdf: "application/pdf"' in script
    assert (
        'docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"' in script
    )
    assert 'new Set(["", "application/octet-stream"])' in script
    assert "FORMAT_CONTROL_CHARACTERS = /\\p{Cf}/u" in script
    assert "FORMAT_CONTROL_CHARACTERS.test(file.name)" in script
    assert "!GENERIC_DOCUMENT_MIME_TYPES.has(declared) && declared !== expected" in script
    assert "file.slice(0, file.size, canonicalMimeType)" in script
    assert "canonicalContent.size !== file.size" in script
    assert "const reviewed = validateSelectedDocument()" in render_source
    assert render_source.index("const reviewed = validateSelectedDocument()") < render_source.index(
        "dom.documentFileSummary.textContent = `${reviewed.file.name}"
    )
    assert 'dom.documentFile.value = ""' in render_source
    assert "const formData = new FormData()" in upload_source
    for field in (
        "file",
        "declared_size",
        "source_timestamp",
        "title",
        "sensitivity",
        "retention_class",
    ):
        assert f'formData.append("{field}"' in upload_source
    assert 'apiFetch("/v1/documents"' in upload_source
    assert 'method: "POST"' in upload_source
    assert "body: formData" in upload_source
    assert 'formData.append("file", canonicalContent, file.name)' in upload_source
    assert 'formData.append("file", file, file.name)' not in upload_source
    assert "Content-Type" not in upload_source
    assert 'typeof requestOptions.body === "string"' in fetch_source
    assert 'headers.set("Authorization", `Bearer ${state.token}`)' in fetch_source

    assert 'payload.kind !== "document.ingest"' in response_source
    assert 'payload.status !== "waiting_approval"' in response_source
    assert 'payload.risk_level !== "local_mutation"' in response_source
    assert "dom.documentUploadTaskId.textContent = payload.id" in upload_source
    assert "dom.documentUploadResult.hidden = false" in upload_source
    assert "state.uploadedTaskId = payload.id" in upload_source
    assert "showUploadedTask" in script

    assert ".innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script


def test_web_readiness_uses_the_session_bearer_token_and_handles_rejection() -> None:
    script = (WEB_STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    readiness_start = script.index("async function refreshReadiness")
    readiness_end = script.index("function configureTransport", readiness_start)
    readiness_source = script[readiness_start:readiness_end]

    assert "if (!state.token || !isSecureTransport())" in readiness_source
    assert 'fetch("/v1/readyz"' in readiness_source
    assert "Authorization: `Bearer ${state.token}`" in readiness_source
    assert "response.status === 401" in readiness_source
    assert 'openAuth("The token was rejected.' in readiness_source
    assert "response.status !== 503" in readiness_source


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
