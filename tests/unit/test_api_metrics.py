"""Security contracts for the global operational metrics endpoint."""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.api.main import get_metrics
from src.config import Settings, get_settings
from src.security import AuthContext


def _auth(*, global_admin: bool) -> AuthContext:
    return AuthContext(
        subject="test-subject",
        roles=frozenset({"admin"}),
        org_ids=frozenset({"*"} if global_admin else {"acme"}),
        auth_enabled=True,
        requested_org_id="acme",
    )


def _request(
    *,
    running: bool = True,
    is_leader: bool = True,
    leadership_state: str = "leader",
) -> SimpleNamespace:
    class Scheduler:
        def status(self) -> dict[str, object]:
            return {
                "enabled": True,
                "running": running,
                "is_leader": is_leader,
                "leadership_state": leadership_state,
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
            }

    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                started_at=0.0,
                request_metrics={
                    "total_requests": 1,
                    "in_flight": 0,
                    "status_counts": defaultdict(int, {200: 1}),
                    "path_counts": defaultdict(int, {"/health/live": 1}),
                    "total_duration_ms": 1.0,
                },
                rate_limiter=None,
                observability_scheduler=Scheduler(),
            )
        )
    )


@pytest.mark.asyncio
async def test_metrics_requires_global_admin() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_metrics(_request(), _auth(global_admin=False))  # type: ignore[arg-type]

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_metrics_redacts_tenant_scheduler_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.api.main.time.time", lambda: 10.0)

    result = await get_metrics(
        _request(),  # type: ignore[arg-type]
        _auth(global_admin=True),
    )

    scheduler = result["scheduler"]
    assert scheduler["health"] == "healthy"
    assert scheduler["org_count"] == 2
    assert "org_ids" not in scheduler
    assert "org_runs" not in scheduler


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
async def test_metrics_projects_replica_leadership_health_truthfully(
    running: bool,
    is_leader: bool,
    leadership_state: str,
    expected_health: str,
) -> None:
    result = await get_metrics(
        _request(
            running=running,
            is_leader=is_leader,
            leadership_state=leadership_state,
        ),  # type: ignore[arg-type]
        _auth(global_admin=True),
    )

    scheduler = result["scheduler"]
    assert scheduler["health"] == expected_health
    assert scheduler["leadership_state"] == leadership_state


def test_metrics_route_authenticates_and_requires_global_scope() -> None:
    settings = Settings(
        api_auth_enabled=True,
        api_require_tenant_header=True,
        api_keys=[
            "viewer-key:reader:viewer:acme",
            "monitor-key:monitor:admin:*",
        ],
    )
    test_app = FastAPI()
    test_app.add_api_route("/metrics", get_metrics, methods=["GET"])
    test_app.dependency_overrides[get_settings] = lambda: settings
    request_state = _request().app.state
    test_app.state.started_at = request_state.started_at
    test_app.state.request_metrics = request_state.request_metrics
    test_app.state.rate_limiter = request_state.rate_limiter
    test_app.state.observability_scheduler = request_state.observability_scheduler

    with TestClient(test_app) as client:
        missing_tenant = client.get(
            "/metrics",
            headers={"X-API-Key": "monitor-key"},
        )
        tenant_viewer = client.get(
            "/metrics",
            headers={"X-API-Key": "viewer-key", "X-Org-Id": "acme"},
        )
        monitor = client.get(
            "/metrics",
            headers={"X-API-Key": "monitor-key", "X-Org-Id": "platform-monitoring"},
        )

    assert missing_tenant.status_code == 400
    assert tenant_viewer.status_code == 403
    assert monitor.status_code == 200
    assert "org_ids" not in monitor.json()["scheduler"]
