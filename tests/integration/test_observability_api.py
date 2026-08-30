"""Integration smoke tests for observability API endpoints."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.config import Settings, get_settings
from src.database import async_session_factory
from src.models.observability import AgentDeployment, AgentSession, SessionStatus


@pytest.fixture()
def client() -> TestClient:
    """Create a TestClient or skip if infrastructure is unavailable."""
    previous_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        api_auth_enabled=False,
        api_require_tenant_header=False,
        api_rate_limit_enabled=False,
        runtime_governance_enabled=True,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    except Exception as exc:  # pragma: no cover - infra dependent
        pytest.skip(f"Observability integration test skipped (infra unavailable): {exc}")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_override


def test_observability_end_to_end_smoke(client: TestClient) -> None:
    """Validate core Phase 1 endpoints together."""
    start = datetime.now(UTC)
    from_ts = start.isoformat()
    to_ts = (start + timedelta(minutes=1)).isoformat()

    org_id = f"smoke-{uuid4().hex[:8]}"
    deployment_key = f"prod-{uuid4().hex[:8]}"

    setup_required_resp = client.get(
        "/api/v1/observability/activation/status",
        params={"org_id": org_id},
    )
    assert setup_required_resp.status_code == 200
    assert setup_required_resp.json()["state"] == "setup_required"
    assert setup_required_resp.json()["deployment_registered"] is False
    assert setup_required_resp.json()["telemetry_received"] is False
    assert setup_required_resp.json()["missing_signals"] == [
        "deployment_registration",
        "agent_telemetry",
    ]

    deployment_resp = client.post(
        "/api/v1/observability/deployments",
        json={
            "org_id": org_id,
            "deployment_key": deployment_key,
            "name": "Smoke Deployment",
            "environment": "prod",
            "runtime": "langgraph",
            "runtime_version": "0.4.2",
            "metadata": {"suite": "integration"},
        },
    )
    assert deployment_resp.status_code == 201
    deployment_id = deployment_resp.json()["id"]
    assert deployment_resp.json()["activation_status"] == "awaiting_telemetry"
    assert deployment_resp.json()["activated_at"] is None

    pending_activation_resp = client.get(
        "/api/v1/observability/activation/status",
        params={"org_id": org_id},
    )
    assert pending_activation_resp.status_code == 200
    assert pending_activation_resp.json()["state"] == "awaiting_telemetry"
    assert pending_activation_resp.json()["deployment_registered"] is True
    assert pending_activation_resp.json()["telemetry_received"] is False
    assert pending_activation_resp.json()["missing_signals"] == ["agent_telemetry"]

    session_resp = client.post(
        "/api/v1/observability/sessions",
        json={
            "deployment_id": deployment_id,
            "agent_id": "smoke-agent",
            "correlation_id": str(uuid4()),
            "started_at": from_ts,
            "tags": ["smoke"],
            "metadata": {"suite": "integration"},
        },
    )
    assert session_resp.status_code == 201
    session_id = session_resp.json()["id"]

    child_session_resp = client.post(
        "/api/v1/observability/sessions",
        json={
            "deployment_id": deployment_id,
            "agent_id": "delegate-agent",
            "correlation_id": str(uuid4()),
            "started_at": from_ts,
            "tags": [],
            "metadata": {},
        },
    )
    assert child_session_resp.status_code == 201
    child_session_id = child_session_resp.json()["id"]

    deployments_resp = client.get(
        "/api/v1/observability/deployments",
        params={"org_id": org_id},
    )
    assert deployments_resp.status_code == 200
    persisted_deployment = next(
        row for row in deployments_resp.json()["deployments"] if row["id"] == deployment_id
    )
    assert persisted_deployment["activation_status"] == "awaiting_telemetry"
    assert persisted_deployment["activated_at"] is None

    policy_resp = client.post(
        "/api/v1/observability/budget-policies",
        json={
            "org_id": org_id,
            "policy_name": "smoke shutdown policy",
            "scope_type": "agent",
            "agent_id": "smoke-agent",
            "period_type": "day",
            "max_actions": 0,
            "action_on_breach": "shutdown",
            "notification_targets": [],
            "metadata": {"suite": "integration"},
            "created_by": "spoofed-policy-actor",
        },
    )
    assert policy_resp.status_code == 201
    assert policy_resp.json()["created_by"] == "local-development"
    policy_id = policy_resp.json()["id"]

    approval_resp = client.post(
        "/api/v1/observability/policy-approvals",
        json={
            "org_id": org_id,
            "policy_id": policy_id,
            "requested_by": "integration-test",
            "reason": "Allow emergency shutdown action for smoke validation",
        },
    )
    assert approval_resp.status_code == 201
    approval_id = approval_resp.json()["id"]
    assert approval_resp.json()["status"] == "pending"
    assert approval_resp.json()["requested_by"] == "local-development"

    approval_decision_resp = client.post(
        f"/api/v1/observability/policy-approvals/{approval_id}/decision",
        json={
            "decision": "approved",
            "decided_by": "integration-admin",
            "reason": "Approved for smoke run",
        },
    )
    assert approval_decision_resp.status_code == 200
    assert approval_decision_resp.json()["status"] == "approved"
    assert approval_decision_resp.json()["decided_by"] == "local-development"

    approvals_list_resp = client.get(
        "/api/v1/observability/policy-approvals",
        params={"org_id": org_id},
    )
    assert approvals_list_resp.status_code == 200
    assert approvals_list_resp.json()["total"] >= 1

    audit_list_resp = client.get(
        "/api/v1/observability/audit/events",
        params={"org_id": org_id},
    )
    assert audit_list_resp.status_code == 200
    assert audit_list_resp.json()["total"] >= 1

    delegation_resp = client.post(
        "/api/v1/observability/delegations",
        json={
            "trace_id": None,
            "parent_session_id": session_id,
            "child_session_id": child_session_id,
            "status": "completed",
            "delegation_reason": "Smoke delegation",
            "requested_capabilities": ["analysis"],
            "started_at": from_ts,
            "completed_at": to_ts,
            "duration_ms": 60000,
            "metadata": {},
        },
    )
    assert delegation_resp.status_code == 201
    reverse_delegation_resp = client.post(
        "/api/v1/observability/delegations",
        json={
            "trace_id": None,
            "parent_session_id": child_session_id,
            "child_session_id": session_id,
            "status": "completed",
            "delegation_reason": "Smoke reverse delegation",
            "requested_capabilities": ["review"],
            "started_at": from_ts,
            "completed_at": to_ts,
            "duration_ms": 60000,
            "metadata": {},
        },
    )
    assert reverse_delegation_resp.status_code == 201

    batch_resp = client.post(
        "/api/v1/observability/actions/batch",
        params={"evaluate_policies": "true"},
        json={
            "session_id": session_id,
            "events": [
                {
                    "client_event_id": str(uuid4()),
                    "action_type": "tool_call",
                    "action_name": "smoke-search",
                    "resource": "https://api.example.com/search",
                    "occurred_at": to_ts,
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "estimated_cost_usd": 0.001,
                    "metadata": {},
                }
            ],
        },
    )
    assert batch_resp.status_code == 202
    assert batch_resp.json()["accepted"] == 1
    assert batch_resp.json()["policy_evaluation"] is not None
    assert batch_resp.json()["policy_evaluation"]["breached_policies"] >= 1

    active_activation_resp = client.get(
        "/api/v1/observability/activation/status",
        params={"org_id": org_id},
    )
    assert active_activation_resp.status_code == 200
    assert active_activation_resp.json()["state"] == "active"
    assert active_activation_resp.json()["telemetry_received"] is True
    assert active_activation_resp.json()["connected_deployments"] == 1
    assert active_activation_resp.json()["active_sessions"] >= 1
    assert active_activation_resp.json()["action_count"] >= 1
    assert active_activation_resp.json()["last_telemetry_at"] is not None
    assert active_activation_resp.json()["missing_signals"] == []

    activated_deployments_resp = client.get(
        "/api/v1/observability/deployments",
        params={"org_id": org_id},
    )
    assert activated_deployments_resp.status_code == 200
    activated_deployment = next(
        row
        for row in activated_deployments_resp.json()["deployments"]
        if row["id"] == deployment_id
    )
    assert activated_deployment["activation_status"] == "activated"
    assert activated_deployment["activated_at"] is not None

    duplicate_client_event_id = str(uuid4())
    duplicate_batch_resp = client.post(
        "/api/v1/observability/actions/batch",
        params={"evaluate_policies": "false"},
        json={
            "session_id": session_id,
            "events": [
                {
                    "client_event_id": duplicate_client_event_id,
                    "action_type": "tool_call",
                    "action_name": "duplicate-check",
                    "resource": "https://api.example.com/duplicate",
                    "occurred_at": to_ts,
                    "input_tokens": 5,
                    "output_tokens": 5,
                    "estimated_cost_usd": 0.0001,
                    "metadata": {},
                },
                {
                    "client_event_id": duplicate_client_event_id,
                    "action_type": "tool_call",
                    "action_name": "duplicate-check",
                    "resource": "https://api.example.com/duplicate",
                    "occurred_at": to_ts,
                    "input_tokens": 5,
                    "output_tokens": 5,
                    "estimated_cost_usd": 0.0001,
                    "metadata": {},
                },
            ],
        },
    )
    assert duplicate_batch_resp.status_code == 202
    assert duplicate_batch_resp.json()["accepted"] == 1
    assert duplicate_batch_resp.json()["rejected"] == 1
    assert any("duplicate client_event_id" in err for err in duplicate_batch_resp.json()["errors"])

    policy_eval_resp = client.post(
        "/api/v1/observability/policies/evaluate",
        json={"org_id": org_id, "execute_actions": False, "notify": True},
    )
    assert policy_eval_resp.status_code == 200
    assert policy_eval_resp.json()["evaluation"]["evaluated_policies"] >= 1
    assert policy_eval_resp.json()["notification_result"]["attempted"] == 0
    assert policy_eval_resp.json()["operation_run_id"] is not None

    policy_events_resp = client.get(
        "/api/v1/observability/budget-policies/events",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert policy_events_resp.status_code == 200
    assert policy_events_resp.json()["total"] >= 1
    policy_events_before_simulation = policy_events_resp.json()["total"]

    simulation_resp = client.post(
        "/api/v1/observability/policies/simulate",
        json={
            "org_id": org_id,
            "from": from_ts,
            "to": to_ts,
            "step_minutes": 1,
            "project_actions": True,
        },
    )
    assert simulation_resp.status_code == 200
    assert simulation_resp.json()["window"]["total_steps"] >= 1
    assert simulation_resp.json()["aggregate"]["side_effects_persisted"] is False
    assert simulation_resp.json()["operation_run_id"] is not None

    policy_events_after_simulation_resp = client.get(
        "/api/v1/observability/budget-policies/events",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert policy_events_after_simulation_resp.status_code == 200
    assert policy_events_after_simulation_resp.json()["total"] == policy_events_before_simulation

    anomaly_resp = client.post(
        "/api/v1/observability/anomalies",
        json={
            "deployment_id": deployment_id,
            "session_id": session_id,
            "anomaly_type": "api_spike",
            "severity": "high",
            "detector_name": "smoke-detector",
            "baseline_value": 1.0,
            "observed_value": 10.0,
            "deviation_ratio": 10.0,
            "score": 0.99,
            "title": "Smoke anomaly",
            "description": "Smoke anomaly for integration test",
            "detected_at": to_ts,
            "metadata": {},
        },
    )
    assert anomaly_resp.status_code == 201
    anomaly_id = anomaly_resp.json()["id"]

    anomaly_detail_resp = client.get(
        f"/api/v1/observability/anomalies/{anomaly_id}",
        params={"org_id": org_id},
    )
    assert anomaly_detail_resp.status_code == 200
    assert anomaly_detail_resp.json()["id"] == anomaly_id

    cross_tenant_anomaly_detail_resp = client.get(
        f"/api/v1/observability/anomalies/{anomaly_id}",
        params={"org_id": f"other-{org_id}"},
    )
    assert cross_tenant_anomaly_detail_resp.status_code == 404

    anomaly_update_resp = client.patch(
        f"/api/v1/observability/anomalies/{anomaly_id}",
        json={
            "status": "open",
            "note": "reviewed by authenticated operator",
            "updated_by": "spoofed-anomaly-actor",
        },
    )
    assert anomaly_update_resp.status_code == 200
    assert anomaly_update_resp.json()["updated_by"] == "local-development"

    dashboard_resp = client.get(
        "/api/v1/observability/dashboard/fleet",
        params={"org_id": org_id, "from": from_ts, "to": to_ts, "granularity": "5m"},
    )
    assert dashboard_resp.status_code == 200
    assert dashboard_resp.json()["totals"]["action_count"] >= 1

    active_resp = client.get("/api/v1/observability/sessions/active", params={"org_id": org_id})
    assert active_resp.status_code == 200
    assert len(active_resp.json()["sessions"]) >= 1
    first_active_session = active_resp.json()["sessions"][0]
    assert "latest_action_at" in first_active_session
    assert "latest_action_type" in first_active_session
    assert "latest_action_name" in first_active_session
    assert "latest_action_resource" in first_active_session

    costs_resp = client.get(
        "/api/v1/observability/costs/summary",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert costs_resp.status_code == 200
    assert (
        costs_resp.json()["totals"]["action_count"]
        == dashboard_resp.json()["totals"]["action_count"]
    )
    assert "cost_per_hour_usd" in costs_resp.json()["totals"]
    assert "projected_daily_cost_usd" in costs_resp.json()["totals"]
    assert "avg_cost_per_action_usd" in costs_resp.json()["totals"]
    if costs_resp.json()["budgets"]:
        first_budget = costs_resp.json()["budgets"][0]
        assert "remaining_budget_usd" in first_budget
        assert "burn_rate_usd_per_hour" in first_budget
        assert "projected_exhaustion_at" in first_budget

    risk_resp = client.get(
        "/api/v1/observability/insights/risk",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert risk_resp.status_code == 200
    assert "current" in risk_resp.json()
    assert "previous" in risk_resp.json()
    assert "delta" in risk_resp.json()
    assert "signals" in risk_resp.json()

    memory_resp = client.get(
        "/api/v1/observability/memory/consistency",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert memory_resp.status_code == 200
    assert "summary" in memory_resp.json()

    anomalies_resp = client.get(
        "/api/v1/observability/anomalies",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert anomalies_resp.status_code == 200
    assert len(anomalies_resp.json()["anomalies"]) >= 1

    anomaly_groups_resp = client.get(
        "/api/v1/observability/anomalies/groups",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert anomaly_groups_resp.status_code == 200
    assert anomaly_groups_resp.json()["total"] >= 1
    first_group = anomaly_groups_resp.json()["groups"][0]
    assert first_group["anomaly_count"] >= 1
    assert first_group["total_occurrences"] >= first_group["anomaly_count"]
    assert first_group["fingerprint"]

    group_ack_resp = client.post(
        "/api/v1/observability/anomalies/groups/status",
        json={
            "org_id": org_id,
            "fingerprint": first_group["fingerprint"],
            "status": "acknowledged",
            "updated_by": "integration-test",
            "note": "ack from integration test",
        },
    )
    assert group_ack_resp.status_code == 200
    assert group_ack_resp.json()["updated_count"] >= 1

    acknowledged_groups_resp = client.get(
        "/api/v1/observability/anomalies/groups",
        params={
            "org_id": org_id,
            "status": "acknowledged",
            "from": from_ts,
            "to": to_ts,
        },
    )
    assert acknowledged_groups_resp.status_code == 200
    assert any(
        group["fingerprint"] == first_group["fingerprint"]
        for group in acknowledged_groups_resp.json()["groups"]
    )

    group_resolve_resp = client.post(
        "/api/v1/observability/anomalies/groups/status",
        json={
            "org_id": org_id,
            "fingerprint": first_group["fingerprint"],
            "status": "resolved",
            "updated_by": "integration-test",
            "note": "resolved from integration test",
        },
    )
    assert group_resolve_resp.status_code == 200
    assert group_resolve_resp.json()["updated_count"] >= 1

    final_anomaly_detail_resp = client.get(
        f"/api/v1/observability/anomalies/{anomaly_id}",
        params={"org_id": org_id},
    )
    assert final_anomaly_detail_resp.status_code == 200
    assert final_anomaly_detail_resp.json()["updated_by"] == "local-development"

    anomaly_audit_resp = client.get(
        "/api/v1/observability/audit/events",
        params={"org_id": org_id, "page_size": 200},
    )
    assert anomaly_audit_resp.status_code == 200
    anomaly_audit_actions = {
        event["action"]: event
        for event in anomaly_audit_resp.json()["events"]
        if event["action"].startswith("anomaly_")
    }
    assert {
        "anomaly_create",
        "anomaly_status_update",
        "anomaly_group_status_update",
    }.issubset(anomaly_audit_actions)
    assert all(
        event["actor_subject"] == "local-development"
        for event in anomaly_audit_actions.values()
    )

    scoped_anomalies_resp = client.get(
        "/api/v1/observability/anomalies",
        params={
            "org_id": org_id,
            "deployment_id": deployment_id,
            "from": from_ts,
            "to": to_ts,
        },
    )
    assert scoped_anomalies_resp.status_code == 200
    assert scoped_anomalies_resp.json()["total"] >= 1

    scoped_groups_resp = client.get(
        "/api/v1/observability/anomalies/groups",
        params={
            "org_id": org_id,
            "deployment_id": deployment_id,
            "from": from_ts,
            "to": to_ts,
        },
    )
    assert scoped_groups_resp.status_code == 200
    assert scoped_groups_resp.json()["total"] >= 1

    detector_resp = client.post(
        "/api/v1/observability/detectors/run",
        json={
            "org_id": org_id,
            "auto_evaluate_policies": True,
            "notify": True,
            "anomaly_dedupe_window_minutes": 60,
            "anomaly_reopen_acknowledged": True,
        },
    )
    assert detector_resp.status_code == 200
    assert "detector_run" in detector_resp.json()
    assert detector_resp.json()["detector_run"]["created_anomalies"] >= 1
    assert detector_resp.json()["notification_result"]["attempted"] == 0
    assert detector_resp.json()["operation_run_id"] is not None

    detector_rerun_resp = client.post(
        "/api/v1/observability/detectors/run",
        json={
            "org_id": org_id,
            "auto_evaluate_policies": False,
            "notify": True,
            "anomaly_dedupe_window_minutes": 60,
            "anomaly_reopen_acknowledged": True,
        },
    )
    assert detector_rerun_resp.status_code == 200
    assert detector_rerun_resp.json()["detector_run"]["created_anomalies"] == 0
    assert detector_rerun_resp.json()["detector_run"]["deduplicated_anomalies"] >= 1
    assert detector_rerun_resp.json()["notification_result"]["skipped"] is True
    assert detector_rerun_resp.json()["notification_result"]["skip_reason"] == "no_actionable_findings"

    post_rerun_groups_resp = client.get(
        "/api/v1/observability/anomalies/groups",
        params={"org_id": org_id, "from": from_ts, "to": to_ts},
    )
    assert post_rerun_groups_resp.status_code == 200
    post_rerun_groups = post_rerun_groups_resp.json()["groups"]
    assert any(
        group["total_occurrences"] > group["anomaly_count"] for group in post_rerun_groups
    )

    ops_resp = client.post(
        "/api/v1/observability/operations/run",
        json={
            "org_id": org_id,
            "run_detectors": True,
            "run_policies": True,
            "execute_policy_actions": False,
            "notify": True,
        },
    )
    assert ops_resp.status_code == 200
    assert "detector_run" in ops_resp.json()
    assert "policy_evaluation" in ops_resp.json()
    ops_run_id = ops_resp.json()["operation_run_id"]
    assert ops_run_id is not None

    ops_status_resp = client.get("/api/v1/observability/operations/status")
    assert ops_status_resp.status_code == 200
    assert "scheduler" in ops_status_resp.json()
    assert "health" in ops_status_resp.json()["scheduler"]

    runs_resp = client.get(
        "/api/v1/observability/operations/runs",
        params={"org_id": org_id},
    )
    assert runs_resp.status_code == 200
    assert runs_resp.json()["total"] >= 1

    run_detail_resp = client.get(f"/api/v1/observability/operations/runs/{ops_run_id}")
    assert run_detail_resp.status_code == 200
    assert run_detail_resp.json()["id"] == ops_run_id

    metrics_resp = client.get("/metrics")
    assert metrics_resp.status_code == 200
    assert "scheduler" in metrics_resp.json()
    assert "health" in metrics_resp.json()["scheduler"]

    siem_export_missing_target_resp = client.post(
        "/api/v1/observability/exports/siem",
        json={
            "org_id": org_id,
            "from": from_ts,
            "to": to_ts,
            "dry_run": False,
        },
    )
    assert siem_export_missing_target_resp.status_code == 400
    assert "notification_targets" in siem_export_missing_target_resp.json()["detail"]

    siem_export_resp = client.post(
        "/api/v1/observability/exports/siem",
        json={
            "org_id": org_id,
            "from": from_ts,
            "to": to_ts,
            "dry_run": True,
            "notification_targets": [
                "https://hooks.example.com/siem",
                "pagerduty:siem-routing-key",
            ],
            "include_anomalies": True,
            "include_policy_events": True,
            "include_operation_runs": True,
            "include_audit_events": True,
        },
    )
    assert siem_export_resp.status_code == 200
    assert siem_export_resp.json()["counts"]["audit_events"] >= 1

    ui_resp = client.get("/api/v1/observability/dashboard/ui")
    assert ui_resp.status_code == 200
    assert "AI Trace Runtime Console" in ui_resp.text
    assert "Active Session Feed" in ui_resp.text
    assert "Risk Signals (Window over Window)" in ui_resp.text


def test_stale_active_sessions_are_excluded_from_product_projections(
    client: TestClient,
) -> None:
    """Validate freshness filtering against PostgreSQL without mutating lifecycle state."""
    threshold_minutes = 30
    now = datetime.now(UTC).replace(microsecond=0)
    fresh_started_at = now - timedelta(minutes=5)
    stale_started_at = now - timedelta(hours=2)
    org_id = f"session-freshness-{uuid4().hex[:8]}"
    deployment_id: str | None = None
    session_ids: list[str] = []
    previous_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        api_auth_enabled=False,
        api_require_tenant_header=False,
        observability_active_session_inactivity_minutes=threshold_minutes,
    )

    async def _clear_activity(session_id: str) -> None:
        async with async_session_factory() as session:
            row = await session.get(AgentSession, UUID(session_id))
            assert row is not None
            row.last_activity_at = None
            await session.commit()

    async def _session_statuses() -> list[str]:
        async with async_session_factory() as session:
            rows = [
                await session.get(AgentSession, UUID(session_id))
                for session_id in session_ids
            ]
            return [
                str(getattr(row.status, "value", row.status))
                for row in rows
                if row is not None
            ]

    async def _cleanup() -> None:
        if deployment_id is None:
            return
        async with async_session_factory() as session:
            deployment = await session.get(AgentDeployment, UUID(deployment_id))
            if deployment is not None:
                await session.delete(deployment)
                await session.commit()

    try:
        deployment_resp = client.post(
            "/api/v1/observability/deployments",
            json={
                "org_id": org_id,
                "deployment_key": f"freshness-{uuid4().hex[:8]}",
                "name": "Session Freshness Integration",
                "environment": "prod",
                "runtime": "integration",
                "metadata": {"suite": "integration", "contract": "session-freshness"},
            },
        )
        assert deployment_resp.status_code == 201
        deployment_id = deployment_resp.json()["id"]

        def create_session(agent_id: str, started_at: datetime) -> str:
            response = client.post(
                "/api/v1/observability/sessions",
                json={
                    "deployment_id": deployment_id,
                    "agent_id": agent_id,
                    "started_at": started_at.isoformat(),
                    "metadata": {"suite": "integration"},
                },
            )
            assert response.status_code == 201
            session_id = str(response.json()["id"])
            session_ids.append(session_id)
            return session_id

        fresh_activity_id = create_session("fresh-explicit-activity", fresh_started_at)
        fresh_started_fallback_id = create_session("fresh-start-fallback", fresh_started_at)
        stale_activity_id = create_session("stale-explicit-activity", stale_started_at)
        stale_started_fallback_id = create_session("stale-start-fallback", stale_started_at)

        far_future = now + timedelta(days=365)
        future_create_resp = client.post(
            "/api/v1/observability/sessions",
            json={
                "deployment_id": deployment_id,
                "agent_id": "future-session",
                "started_at": far_future.isoformat(),
            },
        )
        assert future_create_resp.status_code == 422
        assert "5 minutes in the future" in future_create_resp.json()["detail"]

        # Exercise COALESCE(last_activity_at, started_at) on both sides of the cutoff.
        client.portal.call(_clear_activity, fresh_started_fallback_id)
        client.portal.call(_clear_activity, stale_started_fallback_id)

        # Delayed heartbeats and backfilled action batches must not move the liveness
        # watermark backward and hide an otherwise live session.
        fresh_watermark = now - timedelta(minutes=1)
        heartbeat_resp = client.patch(
            f"/api/v1/observability/sessions/{fresh_activity_id}",
            json={"last_activity_at": fresh_watermark.isoformat()},
        )
        assert heartbeat_resp.status_code == 200
        delayed_heartbeat_resp = client.patch(
            f"/api/v1/observability/sessions/{fresh_activity_id}",
            json={"last_activity_at": stale_started_at.isoformat()},
        )
        assert delayed_heartbeat_resp.status_code == 200
        future_heartbeat_resp = client.patch(
            f"/api/v1/observability/sessions/{fresh_activity_id}",
            json={"last_activity_at": far_future.isoformat()},
        )
        assert future_heartbeat_resp.status_code == 422
        delayed_batch_resp = client.post(
            "/api/v1/observability/actions/batch",
            params={"evaluate_policies": "false"},
            json={
                "session_id": fresh_activity_id,
                "events": [
                    {
                        "client_event_id": str(uuid4()),
                        "action_type": "tool_call",
                        "action_name": "delayed-backfill",
                        "occurred_at": stale_started_at.isoformat(),
                        "metadata": {"contract": "monotonic-activity"},
                    }
                ],
            },
        )
        assert delayed_batch_resp.status_code == 202
        future_batch_resp = client.post(
            "/api/v1/observability/actions/batch",
            params={"evaluate_policies": "false"},
            json={
                "session_id": fresh_activity_id,
                "events": [
                    {
                        "client_event_id": str(uuid4()),
                        "action_type": "tool_call",
                        "action_name": "future-event",
                        "occurred_at": far_future.isoformat(),
                    }
                ],
            },
        )
        assert future_batch_resp.status_code == 422

        active_resp = client.get(
            "/api/v1/observability/sessions/active",
            params={"org_id": org_id},
        )
        assert active_resp.status_code == 200
        active_payload = active_resp.json()
        assert {item["id"] for item in active_payload["sessions"]} == {
            fresh_activity_id,
            fresh_started_fallback_id,
        }
        assert stale_activity_id not in {item["id"] for item in active_payload["sessions"]}
        assert active_payload["total"] == 2
        assert active_payload["inactivity_threshold_minutes"] == threshold_minutes
        assert active_payload["stale_active_sessions_excluded"] == 2
        fresh_item = next(
            item for item in active_payload["sessions"] if item["id"] == fresh_activity_id
        )
        assert datetime.fromisoformat(fresh_item["last_activity_at"]) == fresh_watermark.replace(
            tzinfo=None
        )

        fleet_resp = client.get(
            "/api/v1/observability/dashboard/fleet",
            params={
                "org_id": org_id,
                "from": (stale_started_at - timedelta(minutes=1)).isoformat(),
                "to": (now + timedelta(minutes=1)).isoformat(),
                "granularity": "5m",
            },
        )
        assert fleet_resp.status_code == 200
        fleet_totals = fleet_resp.json()["totals"]
        assert fleet_totals["active_sessions"] == 2
        assert fleet_totals["active_session_inactivity_minutes"] == threshold_minutes
        assert fleet_totals["stale_active_sessions_excluded"] == 2

        assert client.portal.call(_session_statuses) == [SessionStatus.ACTIVE.value] * 4
    finally:
        client.portal.call(_cleanup)
        if previous_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_override
