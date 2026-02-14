"""Integration smoke tests for observability API endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture()
def client() -> TestClient:
    """Create a TestClient or skip if infrastructure is unavailable."""
    try:
        with TestClient(app) as test_client:
            yield test_client
    except Exception as exc:  # pragma: no cover - infra dependent
        pytest.skip(f"Observability integration test skipped (infra unavailable): {exc}")


def test_observability_end_to_end_smoke(client: TestClient) -> None:
    """Validate core Phase 1 endpoints together."""
    start = datetime.now(timezone.utc)
    from_ts = start.isoformat()
    to_ts = (start + timedelta(minutes=1)).isoformat()

    org_id = f"smoke-{uuid4().hex[:8]}"
    deployment_key = f"prod-{uuid4().hex[:8]}"

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
        },
    )
    assert policy_resp.status_code == 201
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
        json={"org_id": org_id, "execute_actions": False},
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
    assert costs_resp.json()["totals"]["action_count"] >= 1
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

    detector_resp = client.post(
        "/api/v1/observability/detectors/run",
        json={
            "org_id": org_id,
            "auto_evaluate_policies": True,
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
