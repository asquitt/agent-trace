"""Focused security contracts for global operations and the embedded dashboard."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.routers.observability import (
    _require_action_org,
    _require_span_org,
    _require_trace_org,
    dashboard_ui,
    get_operations_status,
    router,
)
from src.config import Settings, get_settings
from src.models.observability import AgentAction, AgentDeployment, AgentSession
from src.models.trace import AITrace, AITraceSpan
from src.security import AuthContext


def _auth(*, roles: set[str], org_ids: set[str]) -> AuthContext:
    return AuthContext(
        subject="test-subject",
        roles=frozenset(roles),
        org_ids=frozenset(org_ids),
        auth_enabled=True,
        requested_org_id="acme",
    )


class _FakeSession:
    def __init__(self, rows: dict[tuple[type[object], UUID], object]) -> None:
        self._rows = rows

    async def get(self, model: type[object], row_id: UUID) -> object | None:
        return self._rows.get((model, row_id))


@pytest.mark.asyncio
async def test_operations_status_requires_global_admin() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    tenant_admin = _auth(roles={"admin"}, org_ids={"acme"})

    with pytest.raises(HTTPException) as exc:
        await get_operations_status(request, tenant_admin)  # type: ignore[arg-type]

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_operations_status_returns_only_public_scheduler_state() -> None:
    class Scheduler:
        def status(self) -> dict[str, object]:
            return {
                "enabled": True,
                "running": True,
                "is_leader": True,
                "leadership_state": "leader",
                "last_leadership_change_at": None,
                "last_leadership_error": None,
                "last_tick_at": None,
                "last_success_at": None,
                "last_error": None,
                "interval_seconds": 60,
                "org_count": 2,
                "failed_orgs": 0,
                "org_ids": ["acme", "contoso"],
                "org_runs": {"acme": {"last_error": None}},
                "last_tick_failures": [{"org_id": "contoso", "error": "secret"}],
            }

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(observability_scheduler=Scheduler()),
        )
    )
    global_admin = _auth(roles={"admin"}, org_ids={"*"})

    result = await get_operations_status(request, global_admin)  # type: ignore[arg-type]

    assert result.scheduler["health"] == "healthy"
    assert result.scheduler["org_count"] == 2
    assert "org_ids" not in result.scheduler
    assert "org_runs" not in result.scheduler
    assert "last_tick_failures" not in result.scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "is_leader", "leadership_state", "expected_health"),
    [
        (True, True, "leader", "healthy"),
        (True, False, "standby", "standby"),
        (True, False, "contending", "contending"),
        (True, False, "error", "error"),
        (False, False, "stopped", "stopped"),
    ],
)
async def test_operations_status_projects_replica_leadership_health_truthfully(
    running: bool,
    is_leader: bool,
    leadership_state: str,
    expected_health: str,
) -> None:
    class Scheduler:
        def status(self) -> dict[str, object]:
            return {
                "enabled": True,
                "running": running,
                "is_leader": is_leader,
                "leadership_state": leadership_state,
                "org_count": 2,
                "failed_orgs": 0,
            }

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(observability_scheduler=Scheduler()),
        )
    )
    global_admin = _auth(roles={"admin"}, org_ids={"*"})

    result = await get_operations_status(request, global_admin)  # type: ignore[arg-type]

    assert result.scheduler["health"] == expected_health
    assert result.scheduler["leadership_state"] == leadership_state


@pytest.mark.asyncio
async def test_dashboard_is_disabled_for_header_authenticated_production_mode() -> None:
    viewer = _auth(roles={"viewer"}, org_ids={"acme"})

    with pytest.raises(HTTPException) as exc:
        await dashboard_ui(viewer, Settings(api_auth_enabled=True))

    assert exc.value.status_code == 503
    assert "browser session authentication" in str(exc.value.detail)


def test_production_dashboard_route_fails_safely_after_authentication() -> None:
    settings = Settings(
        api_auth_enabled=True,
        api_require_tenant_header=True,
        api_keys=["viewer-key:dashboard-user:viewer:acme"],
    )
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_settings] = lambda: settings

    with TestClient(test_app) as client:
        unauthenticated = client.get("/api/v1/observability/dashboard/ui")
        authenticated = client.get(
            "/api/v1/observability/dashboard/ui",
            headers={"X-API-Key": "viewer-key", "X-Org-Id": "acme"},
        )

    assert unauthenticated.status_code == 400
    assert authenticated.status_code == 503
    assert "browser session authentication" in authenticated.json()["detail"]


@pytest.mark.asyncio
async def test_development_dashboard_uses_safe_dom_and_security_headers() -> None:
    local_admin = AuthContext(
        subject="local-development",
        roles=frozenset({"viewer", "operator", "admin"}),
        org_ids=frozenset({"*"}),
        auth_enabled=False,
    )

    response = await dashboard_ui(local_admin, Settings(api_auth_enabled=False))
    html = response.body.decode("utf-8")

    assert response.status_code == 200
    assert "Content-Security-Policy" in response.headers
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "innerHTML" not in html
    assert "textContent" in html
    assert 'label for="orgId"' in html


@pytest.mark.asyncio
async def test_linked_trace_cannot_cross_tenant_through_its_session() -> None:
    deployment_id = uuid4()
    session_id = uuid4()
    trace_id = uuid4()
    session = _FakeSession(
        {
            (AITrace, trace_id): SimpleNamespace(
                id=trace_id,
                org_id=None,
                session_id=session_id,
                deployment_id=None,
            ),
            (AgentSession, session_id): SimpleNamespace(
                id=session_id,
                deployment_id=deployment_id,
            ),
            (AgentDeployment, deployment_id): SimpleNamespace(
                id=deployment_id,
                org_id="contoso",
            ),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await _require_trace_org(  # type: ignore[arg-type]
            session,
            trace_id,
            "acme",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_linked_action_cannot_cross_tenant_through_its_session() -> None:
    deployment_id = uuid4()
    session_id = uuid4()
    action_id = uuid4()
    session = _FakeSession(
        {
            (AgentAction, action_id): SimpleNamespace(
                id=action_id,
                session_id=session_id,
            ),
            (AgentSession, session_id): SimpleNamespace(
                id=session_id,
                deployment_id=deployment_id,
            ),
            (AgentDeployment, deployment_id): SimpleNamespace(
                id=deployment_id,
                org_id="contoso",
            ),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await _require_action_org(  # type: ignore[arg-type]
            session,
            action_id,
            "acme",
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_linked_span_cannot_cross_tenant_through_its_trace() -> None:
    trace_id = uuid4()
    span_id = uuid4()
    session = _FakeSession(
        {
            (AITraceSpan, span_id): SimpleNamespace(
                id=span_id,
                trace_id=trace_id,
                session_id=None,
            ),
            (AITrace, trace_id): SimpleNamespace(
                id=trace_id,
                org_id="contoso",
                session_id=None,
                deployment_id=None,
            ),
        }
    )

    with pytest.raises(HTTPException) as exc:
        await _require_span_org(  # type: ignore[arg-type]
            session,
            span_id,
            "acme",
        )

    assert exc.value.status_code == 400
