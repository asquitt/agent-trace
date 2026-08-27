"""PostgreSQL-backed browser authentication and authorization flow."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.api.main import app
from src.config import Settings, get_settings
from src.database import async_session_factory
from src.models.auth import BrowserSession
from src.models.observability import AgentDeployment
from src.services.browser_sessions import hash_browser_secret
from src.utils.time import utc_now_naive


@pytest.fixture()
def browser_client() -> tuple[TestClient, Settings]:
    settings = Settings(
        api_auth_enabled=True,
        api_require_tenant_header=True,
        api_keys=["browser-test-key:console-user:viewer|operator:acme"],
        browser_session_cookie_secure=True,
        browser_session_ttl_minutes=30,
        observability_scheduler_enabled=False,
    )
    previous_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client, settings
    except Exception as exc:  # pragma: no cover - infrastructure dependent
        pytest.skip(f"Browser-auth integration test skipped (infra unavailable): {exc}")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_override


def test_browser_session_exchange_csrf_tenancy_and_revocation(
    browser_client: tuple[TestClient, Settings],
) -> None:
    client, settings = browser_client
    org_id = "acme"
    deployment_key = f"browser-auth-{uuid4().hex[:10]}"

    invalid = client.post(
        "/api/v1/auth/browser/sessions",
        json={"api_key": "not-valid"},
    )
    assert invalid.status_code == 401
    assert invalid.json() == {"detail": "Invalid credentials"}
    assert settings.browser_session_cookie_name not in invalid.headers.get("set-cookie", "")

    login = client.post(
        "/api/v1/auth/browser/sessions",
        json={"api_key": "browser-test-key"},
    )
    assert login.status_code == 201
    assert login.json()["subject"] == "console-user"
    assert login.json()["roles"] == ["operator", "viewer"]
    assert login.json()["org_ids"] == [org_id]
    assert "api_key" not in login.text

    set_cookie_headers = login.headers.get_list("set-cookie")
    session_cookie_header = next(
        item for item in set_cookie_headers if item.startswith(settings.browser_session_cookie_name)
    )
    assert "HttpOnly" in session_cookie_header
    assert "Secure" in session_cookie_header
    assert "SameSite=strict" in session_cookie_header
    assert "Domain=" not in session_cookie_header

    session_token = client.cookies.get(settings.browser_session_cookie_name)
    csrf_token = client.cookies.get(settings.browser_csrf_cookie_name)
    assert session_token
    assert csrf_token
    assert login.json()["csrf_token"] == csrf_token

    current = client.get("/api/v1/auth/browser/session")
    assert current.status_code == 200
    assert current.json()["authentication_method"] == "browser_session"
    assert current.json()["csrf_token"] == csrf_token
    assert current.headers["cache-control"] == "no-store"

    no_tenant = client.get("/api/v1/observability/deployments", params={"org_id": org_id})
    assert no_tenant.status_code == 400

    cross_tenant = client.get(
        "/api/v1/observability/deployments",
        params={"org_id": "contoso"},
        headers={settings.api_tenant_header: "contoso"},
    )
    assert cross_tenant.status_code == 403

    deployment_payload = {
        "org_id": org_id,
        "deployment_key": deployment_key,
        "name": "Browser Auth Integration",
        "environment": "dev",
        "runtime": "pytest",
        "metadata": {"suite": "browser-auth"},
    }
    missing_csrf = client.post(
        "/api/v1/observability/deployments",
        json=deployment_payload,
        headers={settings.api_tenant_header: org_id},
    )
    assert missing_csrf.status_code == 403

    created = client.post(
        "/api/v1/observability/deployments",
        json=deployment_payload,
        headers={
            settings.api_tenant_header: org_id,
            settings.browser_csrf_header: csrf_token,
        },
    )
    assert created.status_code == 201
    deployment_id = created.json()["id"]

    async def _assert_persisted_hashes() -> None:
        async with async_session_factory() as db:
            row = (
                await db.execute(
                    select(BrowserSession).where(BrowserSession.subject == "console-user")
                )
            ).scalar_one()
            assert row.token_hash == hash_browser_secret(session_token)
            assert row.csrf_token_hash == hash_browser_secret(csrf_token)
            assert session_token not in {row.token_hash, row.csrf_token_hash}
            assert csrf_token not in {row.token_hash, row.csrf_token_hash}

    client.portal.call(_assert_persisted_hashes)

    logout_without_csrf = client.delete("/api/v1/auth/browser/session")
    assert logout_without_csrf.status_code == 403

    logout = client.delete(
        "/api/v1/auth/browser/session",
        headers={settings.browser_csrf_header: csrf_token},
    )
    assert logout.status_code == 204

    missing_session = client.get("/api/v1/auth/browser/session")
    assert missing_session.status_code == 401
    assert missing_session.json() == {"detail": "Invalid or expired browser session"}

    replay = client.get(
        "/api/v1/observability/deployments",
        params={"org_id": org_id},
        headers={
            "Cookie": f"{settings.browser_session_cookie_name}={session_token}",
            settings.api_tenant_header: org_id,
        },
    )
    assert replay.status_code == 401

    revoked_session = client.get(
        "/api/v1/auth/browser/session",
        headers={"Cookie": f"{settings.browser_session_cookie_name}={session_token}"},
    )
    assert revoked_session.status_code == 401

    expired_token = f"expired-{uuid4().hex}"

    async def _insert_expired_session() -> None:
        async with async_session_factory() as db:
            db.add(
                BrowserSession(
                    token_hash=hash_browser_secret(expired_token),
                    csrf_token_hash=hash_browser_secret("expired-csrf"),
                    subject="expired-user",
                    roles=["viewer"],
                    org_ids=[org_id],
                    expires_at=utc_now_naive() - timedelta(seconds=1),
                )
            )
            await db.commit()

    client.portal.call(_insert_expired_session)
    expired_replay = client.get(
        "/api/v1/auth/browser/session",
        headers={"Cookie": f"{settings.browser_session_cookie_name}={expired_token}"},
    )
    assert expired_replay.status_code == 401

    async def _cleanup() -> None:
        async with async_session_factory() as db:
            await db.execute(delete(BrowserSession).where(BrowserSession.subject == "console-user"))
            await db.execute(delete(BrowserSession).where(BrowserSession.subject == "expired-user"))
            await db.execute(delete(AgentDeployment).where(AgentDeployment.id == deployment_id))
            await db.commit()

    client.portal.call(_cleanup)
