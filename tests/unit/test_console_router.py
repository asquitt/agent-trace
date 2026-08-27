"""Operator console hosting contracts."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers.console import router
from src.config import Settings, get_settings


def _app_for(dist_dir: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        operator_console_dist_dir=str(dist_dir),
    )
    return app


def test_console_serves_secure_spa_and_immutable_assets(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<main>AI Trace console</main>", encoding="utf-8")
    (assets_dir / "app-abc123.js").write_text("export {};", encoding="utf-8")

    with TestClient(_app_for(dist_dir)) as client:
        index = client.get("/console/")
        nested = client.get("/console/anomalies/example")
        asset = client.get("/console/assets/app-abc123.js")
        missing_asset = client.get("/console/assets/missing.js")

    assert index.status_code == 200
    assert nested.status_code == 200
    assert index.text == nested.text
    assert index.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert missing_asset.status_code == 404


def test_console_fails_truthfully_without_built_assets(tmp_path: Path) -> None:
    with TestClient(_app_for(tmp_path / "missing")) as client:
        response = client.get("/console/")

    assert response.status_code == 503
    assert response.json()["detail"] == "Operator console assets are unavailable"
