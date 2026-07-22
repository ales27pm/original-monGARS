from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

WEB_STATIC_ROOT = Path(__file__).resolve().parents[2] / "web" / "static"

_ASSET_MEDIA_TYPES = {
    "app.css": "text/css",
    "app.js": "text/javascript",
}
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_INDEX_HEADERS = {
    **_NO_STORE_HEADERS,
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
}


def _contained_file(static_root: Path, relative_name: str) -> Path | None:
    try:
        resolved_root = static_root.resolve(strict=True)
        candidate = (resolved_root / relative_name).resolve(strict=True)
        candidate.relative_to(resolved_root)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def create_web_router(*, static_root: Path = WEB_STATIC_ROOT) -> APIRouter:
    """Create the bundled UI routes without exposing the package filesystem."""

    router = APIRouter(include_in_schema=False)

    @router.get("/")
    async def web_index() -> FileResponse:
        index = _contained_file(static_root, "index.html")
        if index is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web interface is unavailable",
            )
        return FileResponse(index, media_type="text/html", headers=_INDEX_HEADERS)

    @router.get("/assets/{asset_name:path}")
    async def web_asset(asset_name: str) -> FileResponse:
        media_type = _ASSET_MEDIA_TYPES.get(asset_name)
        if media_type is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        asset = _contained_file(static_root, asset_name)
        if asset is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        return FileResponse(asset, media_type=media_type, headers=_NO_STORE_HEADERS)

    return router


router = create_web_router()
