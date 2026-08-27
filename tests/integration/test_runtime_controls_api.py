"""PostgreSQL-backed runtime-control delivery and acknowledgement contract."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from src.api.main import app
from src.config import Settings, get_settings
from src.database import async_session_factory
from src.models.auth import BrowserSession
from src.models.observability import (
    AgentDeployment,
    AgentSession,
    BudgetPeriodType,
    BudgetPolicy,
    BudgetScopeType,
    DeploymentEnvironment,
    PolicyActionApproval,
    PolicyActionType,
    PolicyApprovalStatus,
    PolicyStatus,
    RuntimeControlRequest,
    SessionStatus,
    SystemAuditEvent,
)
from src.services.runtime_controls import claim_runtime_controls
from src.utils.time import utc_now_naive


@pytest.fixture()
def runtime_client() -> tuple[TestClient, Settings]:
    settings = Settings(
        api_auth_enabled=True,
        api_require_tenant_header=True,
        api_keys=[
            "runtime-key:runtime-acme:operator:acme",
            "viewer-key:runtime-viewer:viewer:acme",
            "other-key:runtime-other:operator:contoso",
        ],
        browser_session_cookie_secure=True,
        observability_scheduler_enabled=False,
        runtime_control_lease_seconds=60,
        runtime_control_max_delivery_attempts=3,
    )
    previous_override = app.dependency_overrides.get(get_settings)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client, settings
    except Exception as exc:  # pragma: no cover - infrastructure dependent
        pytest.skip(f"Runtime-control integration test skipped (infra unavailable): {exc}")
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_settings, None)
        else:
            app.dependency_overrides[get_settings] = previous_override


def test_runtime_control_tenant_lease_ack_and_shutdown_projection(
    runtime_client: tuple[TestClient, Settings],
) -> None:
    client, settings = runtime_client
    now = utc_now_naive()
    deployment_id = uuid4()
    throttle_session_id = uuid4()
    shutdown_session_id = uuid4()
    expired_session_id = uuid4()
    reclaim_session_id = uuid4()
    concurrent_session_id = uuid4()
    throttle_id = uuid4()
    shutdown_id = uuid4()
    expired_id = uuid4()
    reclaim_id = uuid4()
    concurrent_id = uuid4()
    approval_id = uuid4()
    expired_approval_id = uuid4()
    policy_id = uuid4()
    expired_policy_id = uuid4()

    async def _seed() -> None:
        async with async_session_factory() as db:
            db.add(
                AgentDeployment(
                    id=deployment_id,
                    org_id="acme",
                    deployment_key=f"runtime-{uuid4().hex}",
                    name="Runtime Control Integration",
                    environment=DeploymentEnvironment.DEV,
                    runtime="pytest",
                    is_active=True,
                    deployment_metadata={},
                )
            )
            db.add_all(
                [
                    AgentSession(
                        id=throttle_session_id,
                        deployment_id=deployment_id,
                        agent_id="throttle-agent",
                        agent_instance_id="instance-throttle",
                        status=SessionStatus.ACTIVE,
                        started_at=now,
                        session_metadata={
                            "control": {"request_id": str(throttle_id), "state": "requested"}
                        },
                    ),
                    AgentSession(
                        id=reclaim_session_id,
                        deployment_id=deployment_id,
                        agent_id="reclaim-agent",
                        agent_instance_id="instance-reclaim",
                        status=SessionStatus.ACTIVE,
                        started_at=now,
                        session_metadata={
                            "control": {"request_id": str(reclaim_id), "state": "requested"}
                        },
                    ),
                    AgentSession(
                        id=concurrent_session_id,
                        deployment_id=deployment_id,
                        agent_id="concurrent-agent",
                        agent_instance_id="instance-concurrent",
                        status=SessionStatus.ACTIVE,
                        started_at=now,
                        session_metadata={
                            "control": {"request_id": str(concurrent_id), "state": "requested"}
                        },
                    ),
                    AgentSession(
                        id=shutdown_session_id,
                        deployment_id=deployment_id,
                        agent_id="shutdown-agent",
                        agent_instance_id="instance-shutdown",
                        status=SessionStatus.ACTIVE,
                        started_at=now,
                        session_metadata={
                            "control": {"request_id": str(shutdown_id), "state": "requested"}
                        },
                    ),
                    AgentSession(
                        id=expired_session_id,
                        deployment_id=deployment_id,
                        agent_id="expired-agent",
                        agent_instance_id="instance-expired",
                        status=SessionStatus.ACTIVE,
                        started_at=now,
                        session_metadata={
                            "control": {"request_id": str(expired_id), "state": "requested"}
                        },
                    ),
                ]
            )
            db.add_all(
                [
                    BudgetPolicy(
                        id=policy_id,
                        org_id="acme",
                        policy_name="approved shutdown",
                        scope_type=BudgetScopeType.ORG,
                        period_type=BudgetPeriodType.DAY,
                        action_on_breach=PolicyActionType.SHUTDOWN,
                        status=PolicyStatus.ACTIVE,
                        notification_targets=[],
                        policy_metadata={},
                    ),
                    BudgetPolicy(
                        id=expired_policy_id,
                        org_id="acme",
                        policy_name="expired shutdown",
                        scope_type=BudgetScopeType.ORG,
                        period_type=BudgetPeriodType.DAY,
                        action_on_breach=PolicyActionType.SHUTDOWN,
                        status=PolicyStatus.ACTIVE,
                        notification_targets=[],
                        policy_metadata={},
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    PolicyActionApproval(
                        id=approval_id,
                        org_id="acme",
                        policy_id=policy_id,
                        action_type=PolicyActionType.SHUTDOWN,
                        status=PolicyApprovalStatus.APPROVED,
                        requested_by="operator",
                        requested_at=now,
                        expires_at=now + timedelta(minutes=5),
                    ),
                    PolicyActionApproval(
                        id=expired_approval_id,
                        org_id="acme",
                        policy_id=expired_policy_id,
                        action_type=PolicyActionType.SHUTDOWN,
                        status=PolicyApprovalStatus.APPROVED,
                        requested_by="operator",
                        requested_at=now - timedelta(minutes=10),
                        expires_at=now - timedelta(minutes=1),
                    ),
                ]
            )
            await db.flush()
            db.add_all(
                [
                    RuntimeControlRequest(
                        id=throttle_id,
                        org_id="acme",
                        deployment_id=deployment_id,
                        session_id=throttle_session_id,
                        action_type="throttle",
                        idempotency_key=uuid4().hex,
                        payload={"action": "throttle", "throttle_rate": 0.25},
                        status="pending",
                        priority=2,
                        available_at=now,
                        acknowledgement_details={},
                    ),
                    RuntimeControlRequest(
                        id=shutdown_id,
                        org_id="acme",
                        deployment_id=deployment_id,
                        session_id=shutdown_session_id,
                        approval_id=approval_id,
                        authorization_expires_at=now + timedelta(minutes=5),
                        action_type="shutdown",
                        idempotency_key=uuid4().hex,
                        payload={"action": "shutdown"},
                        status="pending",
                        priority=4,
                        available_at=now,
                        acknowledgement_details={},
                    ),
                    RuntimeControlRequest(
                        id=expired_id,
                        org_id="acme",
                        deployment_id=deployment_id,
                        session_id=expired_session_id,
                        approval_id=expired_approval_id,
                        authorization_expires_at=now - timedelta(minutes=1),
                        action_type="shutdown",
                        idempotency_key=uuid4().hex,
                        payload={"action": "shutdown"},
                        status="pending",
                        priority=4,
                        available_at=now,
                        acknowledgement_details={},
                    ),
                    RuntimeControlRequest(
                        id=reclaim_id,
                        org_id="acme",
                        deployment_id=deployment_id,
                        session_id=reclaim_session_id,
                        action_type="throttle",
                        idempotency_key=uuid4().hex,
                        payload={"action": "throttle", "throttle_rate": 0.5},
                        status="leased",
                        priority=2,
                        available_at=now,
                        lease_token_hash=hashlib.sha256(("o" * 43).encode()).hexdigest(),
                        lease_owner_subject="runtime-acme",
                        lease_runtime_instance_id="instance-reclaim",
                        lease_expires_at=now - timedelta(seconds=1),
                        delivery_attempts=2,
                        acknowledgement_details={},
                    ),
                    RuntimeControlRequest(
                        id=concurrent_id,
                        org_id="acme",
                        deployment_id=deployment_id,
                        session_id=concurrent_session_id,
                        action_type="throttle",
                        idempotency_key=uuid4().hex,
                        payload={"action": "throttle", "throttle_rate": 0.75},
                        status="pending",
                        priority=2,
                        available_at=now,
                        acknowledgement_details={},
                    ),
                ]
            )
            await db.commit()

    client.portal.call(_seed)
    headers = {"X-API-Key": "runtime-key", settings.api_tenant_header: "acme"}
    throttle_claim = {
        "org_id": "acme",
        "deployment_id": str(deployment_id),
        "session_id": str(throttle_session_id),
        "runtime_instance_id": "instance-throttle",
        "max_items": 10,
    }

    viewer = client.post(
        "/api/v1/runtime-controls/claim",
        json=throttle_claim,
        headers={"X-API-Key": "viewer-key", settings.api_tenant_header: "acme"},
    )
    assert viewer.status_code == 403

    cross_tenant = client.post(
        "/api/v1/runtime-controls/claim",
        json=throttle_claim,
        headers={"X-API-Key": "other-key", settings.api_tenant_header: "acme"},
    )
    assert cross_tenant.status_code == 403

    wrong_instance = client.post(
        "/api/v1/runtime-controls/claim",
        json={**throttle_claim, "runtime_instance_id": "wrong-instance"},
        headers=headers,
    )
    assert wrong_instance.status_code == 409

    login = client.post(
        "/api/v1/auth/browser/sessions",
        json={"api_key": "runtime-key"},
    )
    assert login.status_code == 201
    browser_denied = client.post(
        "/api/v1/runtime-controls/claim",
        json=throttle_claim,
        headers={
            settings.api_tenant_header: "acme",
            settings.browser_csrf_header: login.json()["csrf_token"],
        },
    )
    assert browser_denied.status_code == 403
    client.cookies.clear()

    claimed = client.post(
        "/api/v1/runtime-controls/claim",
        json=throttle_claim,
        headers=headers,
    )
    assert claimed.status_code == 200
    controls = claimed.json()["controls"]
    assert [row["id"] for row in controls] == [str(throttle_id)]
    lease_token = controls[0]["lease_token"]

    async def _assert_token_is_hashed() -> None:
        async with async_session_factory() as db:
            row = await db.get(RuntimeControlRequest, throttle_id)
            assert row is not None
            assert row.lease_token_hash
            assert row.lease_token_hash != lease_token
            audits = list(
                (
                    await db.execute(
                        select(SystemAuditEvent).where(
                            SystemAuditEvent.resource_id == str(throttle_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert lease_token not in str([audit.details for audit in audits])

    client.portal.call(_assert_token_is_hashed)

    wrong_token = client.post(
        f"/api/v1/runtime-controls/{throttle_id}/ack",
        json={
            "org_id": "acme",
            "runtime_instance_id": "instance-throttle",
            "lease_token": "x" * 43,
            "acknowledgement_id": "ack-throttle-1",
            "outcome": "applied",
            "details": {"effective_rate": 0.25},
        },
        headers=headers,
    )
    assert wrong_token.status_code == 409

    ack_payload = {
        "org_id": "acme",
        "runtime_instance_id": "instance-throttle",
        "lease_token": lease_token,
        "acknowledgement_id": "ack-throttle-1",
        "outcome": "applied",
        "details": {"effective_rate": 0.25},
    }
    acknowledged = client.post(
        f"/api/v1/runtime-controls/{throttle_id}/ack",
        json=ack_payload,
        headers=headers,
    )
    assert acknowledged.status_code == 200
    assert acknowledged.json()["control"]["status"] == "applied"
    assert acknowledged.json()["idempotent_replay"] is False

    replay = client.post(
        f"/api/v1/runtime-controls/{throttle_id}/ack",
        json=ack_payload,
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    changed_replay = client.post(
        f"/api/v1/runtime-controls/{throttle_id}/ack",
        json={**ack_payload, "details": {"effective_rate": 0.5}},
        headers=headers,
    )
    assert changed_replay.status_code == 409

    shutdown_claim = client.post(
        "/api/v1/runtime-controls/claim",
        json={
            "org_id": "acme",
            "deployment_id": str(deployment_id),
            "session_id": str(shutdown_session_id),
            "runtime_instance_id": "instance-shutdown",
        },
        headers=headers,
    )
    assert shutdown_claim.status_code == 200
    shutdown_controls = shutdown_claim.json()["controls"]
    assert [row["id"] for row in shutdown_controls] == [str(shutdown_id)]
    shutdown_ack = client.post(
        f"/api/v1/runtime-controls/{shutdown_id}/ack",
        json={
            "org_id": "acme",
            "runtime_instance_id": "instance-shutdown",
            "lease_token": shutdown_controls[0]["lease_token"],
            "acknowledgement_id": "ack-shutdown-1",
            "outcome": "applied",
            "details": {"signal": "graceful-stop"},
        },
        headers=headers,
    )
    assert shutdown_ack.status_code == 200

    expired_claim = client.post(
        "/api/v1/runtime-controls/claim",
        json={
            "org_id": "acme",
            "deployment_id": str(deployment_id),
            "session_id": str(expired_session_id),
            "runtime_instance_id": "instance-expired",
        },
        headers=headers,
    )
    assert expired_claim.status_code == 200
    assert expired_claim.json()["controls"] == []

    reclaimed = client.post(
        "/api/v1/runtime-controls/claim",
        json={
            "org_id": "acme",
            "deployment_id": str(deployment_id),
            "session_id": str(reclaim_session_id),
            "runtime_instance_id": "instance-reclaim",
        },
        headers=headers,
    )
    assert reclaimed.status_code == 200
    reclaim_controls = reclaimed.json()["controls"]
    assert [row["id"] for row in reclaim_controls] == [str(reclaim_id)]
    assert reclaim_controls[0]["delivery_attempts"] == 3
    assert reclaim_controls[0]["lease_token"] != "o" * 43
    stale_ack = client.post(
        f"/api/v1/runtime-controls/{reclaim_id}/ack",
        json={
            "org_id": "acme",
            "runtime_instance_id": "instance-reclaim",
            "lease_token": "o" * 43,
            "acknowledgement_id": "ack-reclaim-stale",
            "outcome": "applied",
            "details": {},
        },
        headers=headers,
    )
    assert stale_ack.status_code == 409

    async def _expire_reclaimed() -> None:
        async with async_session_factory() as db:
            row = await db.get(RuntimeControlRequest, reclaim_id)
            assert row is not None
            row.lease_expires_at = utc_now_naive() - timedelta(seconds=1)
            await db.commit()

    client.portal.call(_expire_reclaimed)
    exhausted = client.post(
        "/api/v1/runtime-controls/claim",
        json={
            "org_id": "acme",
            "deployment_id": str(deployment_id),
            "session_id": str(reclaim_session_id),
            "runtime_instance_id": "instance-reclaim",
        },
        headers=headers,
    )
    assert exhausted.status_code == 200
    assert exhausted.json()["controls"] == []

    async def _claim_concurrently() -> list[list[dict[str, object]]]:
        async def _claim_once() -> list[dict[str, object]]:
            async with async_session_factory() as db:
                rows = await claim_runtime_controls(
                    db,
                    org_id="acme",
                    deployment_id=deployment_id,
                    session_id=concurrent_session_id,
                    runtime_instance_id="instance-concurrent",
                    actor_subject="runtime-acme",
                    actor_roles=["operator"],
                    lease_seconds=60,
                    max_delivery_attempts=3,
                    max_items=10,
                )
                await db.commit()
                return rows

        first, second = await asyncio.gather(_claim_once(), _claim_once())
        return [first, second]

    concurrent_claims = client.portal.call(_claim_concurrently)
    assert sorted(len(rows) for rows in concurrent_claims) == [0, 1]
    delivered = [row for rows in concurrent_claims for row in rows]
    assert [row["id"] for row in delivered] == [str(concurrent_id)]

    async def _assert_projections_and_cleanup() -> None:
        async with async_session_factory() as db:
            throttle_session = await db.get(AgentSession, throttle_session_id)
            shutdown_session = await db.get(AgentSession, shutdown_session_id)
            assert throttle_session is not None
            assert throttle_session.status == SessionStatus.ACTIVE
            assert throttle_session.session_metadata["control"]["state"] == "applied"
            assert shutdown_session is not None
            assert shutdown_session.status == SessionStatus.TERMINATED
            assert shutdown_session.ended_at is not None
            reclaim_control = await db.get(RuntimeControlRequest, reclaim_id)
            assert reclaim_control is not None
            assert reclaim_control.status == "failed"
            assert reclaim_control.failure_reason == "delivery_attempts_exhausted"
            expired_control = await db.get(RuntimeControlRequest, expired_id)
            assert expired_control is not None
            assert expired_control.status == "failed"
            assert expired_control.failure_reason == "authorization_invalid_or_expired"
            await db.execute(delete(BrowserSession).where(BrowserSession.subject == "runtime-acme"))
            await db.execute(delete(AgentDeployment).where(AgentDeployment.id == deployment_id))
            await db.commit()

    client.portal.call(_assert_projections_and_cleanup)
