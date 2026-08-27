"""Same-origin hosting for the operator console."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ...dependencies import SettingsDep

router = APIRouter(tags=["operator-console"])

_CONSOLE_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'; form-action 'self'; "
        "script-src 'self'; style-src 'self'; font-src 'self'; "
        "img-src 'self' data:; connect-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _console_response(path: str, settings: SettingsDep) -> Response:
    dist_dir = Path(settings.operator_console_dist_dir).resolve()
    index_path = dist_dir / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="Operator console assets are unavailable")

    requested_path = (dist_dir / path).resolve() if path else index_path
    is_safe_file = requested_path.is_relative_to(dist_dir) and requested_path.is_file()
    is_asset_request = path.startswith("assets/") or bool(Path(path).suffix)
    if is_asset_request and not is_safe_file:
        raise HTTPException(status_code=404, detail="Console asset not found")

    response = FileResponse(requested_path if is_safe_file else index_path)
    for name, value in _CONSOLE_SECURITY_HEADERS.items():
        response.headers[name] = value
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable"
        if is_safe_file and path.startswith("assets/")
        else "no-store"
    )
    return response


@router.get("/console", include_in_schema=False)
@router.get("/console/", include_in_schema=False)
async def console_index(settings: SettingsDep) -> Response:
    """Serve the operator console entrypoint."""
    return _console_response("", settings)


@router.get("/console/{path:path}", include_in_schema=False)
async def console_route(path: str, settings: SettingsDep) -> Response:
    """Serve a console asset or fall back to the SPA entrypoint."""
    return _console_response(path, settings)
