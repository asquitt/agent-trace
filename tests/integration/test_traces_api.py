"""Integration smoke tests for trace query API endpoints."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.config import Settings, get_settings
from src.database import async_session_factory
from src.dependencies import get_storage_backend
from src.models.idea import Idea, SourceType
from src.models.observability import (
    AgentDeployment,
    AgentSession,
    DeploymentEnvironment,
    SessionStatus,
)
from src.models.trace import SpanStatus, SpanType, TraceStatus, TraceType


@pytest.fixture()
def client() -> TestClient:
    """Create a TestClient or skip if infrastructure is unavailable."""
    previous_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None,
        api_auth_enabled=False,
        api_require_tenant_header=False,
        api_rate_limit_enabled=False,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    except Exception as exc:  # pragma: no cover - infra dependent
        pytest.skip(f"Trace integration test skipped (infra unavailable): {exc}")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_override


def _seed_trace_data(
    client: TestClient,
    org_id: str,
) -> tuple[str, str, int, str, str, str, str, str, str]:
    """Seed trace rows for integration validation."""
    storage = get_storage_backend()
    primary_trace_id = uuid4()
    failed_trace_id = uuid4()
    primary_correlation_id = uuid4()
    deployment_id = uuid4()
    session_id = uuid4()
    span_id = uuid4()
    primary_idea_id = 0
    secondary_idea_id = 0
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

    async def _seed() -> None:
        nonlocal primary_idea_id, secondary_idea_id
        async with async_session_factory() as session:
            primary_idea = Idea(
                source_type=SourceType.MANUAL,
                source_id=f"trace-seed-{uuid4().hex[:12]}",
                name="Trace Integration Primary Idea",
                description="Primary idea fixture",
                scraped_at=now,
            )
            secondary_idea = Idea(
                source_type=SourceType.MANUAL,
                source_id=f"trace-seed-{uuid4().hex[:12]}",
                name="Trace Integration Secondary Idea",
                description="Secondary idea fixture",
                scraped_at=now,
            )
            session.add_all([primary_idea, secondary_idea])
            await session.flush()
            primary_idea_id = int(primary_idea.id)
            secondary_idea_id = int(secondary_idea.id)
            session.add(
                AgentDeployment(
                    id=deployment_id,
                    org_id=org_id,
                    deployment_key=f"trace-deployment-{uuid4().hex[:12]}",
                    name="Trace integration deployment",
                    environment=DeploymentEnvironment.PROD,
                    runtime="integration",
                    is_active=True,
                    deployment_metadata={"suite": "integration"},
                )
            )
            session.add(
                AgentSession(
                    id=session_id,
                    deployment_id=deployment_id,
                    agent_id="trace-primary-agent",
                    status=SessionStatus.ACTIVE,
                    started_at=now - timedelta(minutes=4),
                    last_activity_at=now - timedelta(minutes=3),
                    session_metadata={"suite": "integration"},
                )
            )
            await session.commit()

        await storage.save_trace(
            {
                "id": str(primary_trace_id),
                "correlation_id": str(primary_correlation_id),
                "trace_type": TraceType.RANKING.value,
                "status": TraceStatus.RUNNING.value,
                "org_id": org_id,
                "deployment_id": str(deployment_id),
                "session_id": str(session_id),
                "agent_id": "trace-primary-agent",
                "idea_id": primary_idea_id,
                "started_at": (now - timedelta(minutes=3)).isoformat(),
                "metadata": {"suite": "integration"},
                "tags": ["trace", "integration"],
            }
        )
        await storage.save_span(
            {
                "id": str(span_id),
                "trace_id": str(primary_trace_id),
                "span_type": SpanType.LLM_CALL.value,
                "name": "integration_span",
                "provider": "anthropic",
                "model": "claude-sonnet-4",
                "system_prompt": "system",
                "user_prompt": "user",
                "assistant_response": "assistant",
                "started_at": (now - timedelta(minutes=3)).isoformat(),
                "status": SpanStatus.RUNNING.value,
                "input_tokens": 120,
                "output_tokens": 30,
                "metadata": {"suite": "integration"},
            }
        )
        await storage.save_reasoning(
            {
                "id": str(uuid4()),
                "span_id": str(span_id),
                "step_number": 1,
                "step_type": "analysis",
                "description": "Primary integration reasoning step",
                "confidence": 0.91,
                "explanation": "Seeded for integration coverage.",
            }
        )
        await storage.update_span(
            span_id,
            {
                "status": SpanStatus.COMPLETED.value,
                "completed_at": (now - timedelta(minutes=3) + timedelta(seconds=2)).isoformat(),
                "duration_ms": 2000,
                "input_tokens": 120,
                "output_tokens": 30,
            },
        )
        await storage.update_trace(
            primary_trace_id,
            {
                "status": TraceStatus.COMPLETED.value,
                "completed_at": (now - timedelta(minutes=3) + timedelta(seconds=3)).isoformat(),
                "duration_ms": 3000,
                "total_input_tokens": 120,
                "total_output_tokens": 30,
                "estimated_cost_usd": 0.25,
            },
        )

        await storage.save_trace(
            {
                "id": str(failed_trace_id),
                "correlation_id": str(uuid4()),
                "trace_type": TraceType.SWOT_ANALYSIS.value,
                "status": TraceStatus.RUNNING.value,
                "org_id": org_id,
                "idea_id": secondary_idea_id,
                "started_at": (now - timedelta(minutes=2)).isoformat(),
                "metadata": {"suite": "integration"},
                "tags": ["trace", "integration", "failure"],
            }
        )
        await storage.update_trace(
            failed_trace_id,
            {
                "status": TraceStatus.FAILED.value,
                "completed_at": (now - timedelta(minutes=2) + timedelta(seconds=2)).isoformat(),
                "duration_ms": 2000,
                "total_input_tokens": 15,
                "total_output_tokens": 3,
                "estimated_cost_usd": 0.04,
                "error_message": "Synthetic integration failure",
            },
        )

    client.portal.call(_seed)
    return (
        str(primary_trace_id),
        str(failed_trace_id),
        primary_idea_id,
        str(primary_correlation_id),
        str(deployment_id),
        str(session_id),
        "trace-primary-agent",
        (now - timedelta(minutes=4)).isoformat(),
        (now - timedelta(minutes=2, seconds=30)).isoformat(),
    )


def test_trace_end_to_end_smoke(client: TestClient) -> None:
    """Validate trace list/detail/reasoning/export/metrics flows together."""
    org_id = f"trace-smoke-{uuid4().hex[:8]}"
    (
        primary_trace_id,
        failed_trace_id,
        primary_idea_id,
        primary_correlation_id,
        deployment_id,
        session_id,
        agent_id,
        primary_window_from,
        primary_window_to,
    ) = _seed_trace_data(client, org_id)

    list_resp = client.get("/api/v1/traces", params={"org_id": org_id, "page_size": 10})
    assert list_resp.status_code == 200
    traces = list_resp.json()["traces"]
    trace_ids = {item["id"] for item in traces}
    assert primary_trace_id in trace_ids
    assert failed_trace_id in trace_ids
    primary_list_item = next(item for item in traces if item["id"] == primary_trace_id)
    assert primary_list_item["org_id"] == org_id
    assert primary_list_item["deployment_id"] == deployment_id
    assert primary_list_item["session_id"] == session_id
    assert primary_list_item["agent_id"] == agent_id

    identity_filter_resp = client.get(
        "/api/v1/traces",
        params={
            "org_id": org_id,
            "deployment_id": deployment_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "from": primary_window_from,
            "to": primary_window_to,
        },
    )
    assert identity_filter_resp.status_code == 200
    assert identity_filter_resp.json()["total"] == 1
    assert identity_filter_resp.json()["traces"][0]["id"] == primary_trace_id

    invalid_window_resp = client.get(
        "/api/v1/traces",
        params={
            "org_id": org_id,
            "from": "2026-08-27T13:00:00+00:00",
            "to": "2026-08-27T12:00:00",
        },
    )
    assert invalid_window_resp.status_code == 400

    idea_filter_resp = client.get(
        "/api/v1/traces",
        params={"org_id": org_id, "idea_id": primary_idea_id},
    )
    assert idea_filter_resp.status_code == 200
    assert idea_filter_resp.json()["total"] == 1
    assert idea_filter_resp.json()["traces"][0]["id"] == primary_trace_id

    correlation_filter_resp = client.get(
        "/api/v1/traces",
        params={"org_id": org_id, "correlation_id": primary_correlation_id},
    )
    assert correlation_filter_resp.status_code == 200
    assert correlation_filter_resp.json()["total"] == 1
    assert correlation_filter_resp.json()["traces"][0]["id"] == primary_trace_id

    detail_resp = client.get(f"/api/v1/traces/{primary_trace_id}", params={"include_prompts": "true"})
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == primary_trace_id
    assert detail["status"] == TraceStatus.COMPLETED.value
    assert detail["org_id"] == org_id
    assert detail["deployment_id"] == deployment_id
    assert detail["session_id"] == session_id
    assert detail["agent_id"] == agent_id
    assert len(detail["spans"]) == 1
    assert detail["spans"][0]["name"] == "integration_span"
    assert detail["spans"][0]["assistant_response"] == "assistant"

    reasoning_resp = client.get(f"/api/v1/traces/{primary_trace_id}/reasoning")
    assert reasoning_resp.status_code == 200
    assert len(reasoning_resp.json()) >= 1

    export_resp = client.get(f"/api/v1/traces/export/{primary_trace_id}/json")
    assert export_resp.status_code == 200
    export_payload = export_resp.json()
    assert export_payload["trace"]["id"] == primary_trace_id
    assert len(export_payload["spans"]) == 1

    metrics_resp = client.get("/api/v1/traces/metrics/summary", params={"org_id": org_id})
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()
    assert metrics["total_traces"] == 2
    assert metrics["successful_traces"] == 1
    assert metrics["failed_traces"] == 1
    assert metrics["total_input_tokens"] == 135
    assert metrics["total_output_tokens"] == 33
    assert metrics["estimated_total_cost_usd"] == pytest.approx(0.29, rel=1e-6)
    assert metrics["avg_duration_ms"] == pytest.approx(2500.0, rel=1e-6)

    metrics_window_resp = client.get(
        "/api/v1/traces/metrics/summary",
        params={
            "org_id": org_id,
            "from": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "to": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        },
    )
    assert metrics_window_resp.status_code == 200
    assert metrics_window_resp.json()["total_traces"] == 2
