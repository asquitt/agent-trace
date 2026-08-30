"""Contracts for AI Trace's internal-only, fail-closed disposition."""

from __future__ import annotations

import inspect
import re
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routers.observability import (
    ActionBatchRequest,
    DetectorRunRequest,
    PolicyEvaluationRequest,
    RuntimeOperationsRunRequest,
    SiemExportRequest,
    evaluate_policies,
    export_siem_events,
    ingest_action_batch,
    run_detectors,
    run_operations_cycle,
)
from src.api.routers.runtime_controls import (
    RuntimeControlAckRequest,
    RuntimeControlClaimRequest,
    acknowledge_control,
    claim_controls,
)
from src.config import Settings
from src.security import AuthContext
from src.services.notification_outbox import NotificationOutboxService
from src.services.observability_runtime import evaluate_budget_policies
from src.services.runtime_controls import (
    acknowledge_runtime_control,
    claim_runtime_controls,
)
from src.services.runtime_governance import RuntimeGovernanceDisabledError

ROOT_DIR = Path(__file__).resolve().parents[2]


def test_side_effecting_request_defaults_are_opt_in() -> None:
    policy = PolicyEvaluationRequest(org_id="acme")
    detector = DetectorRunRequest(org_id="acme")
    operations = RuntimeOperationsRunRequest(org_id="acme")
    query_default = inspect.signature(ingest_action_batch).parameters[
        "evaluate_policies_flag"
    ].default

    assert policy.execute_actions is False
    assert policy.notify is False
    assert detector.auto_evaluate_policies is False
    assert detector.execute_policy_actions is False
    assert detector.notify is False
    assert operations.run_detectors is True
    assert operations.run_policies is False
    assert operations.execute_policy_actions is False
    assert operations.notify is False
    assert getattr(query_default, "default", None) is False


@pytest.mark.asyncio
async def test_frozen_outbox_rejects_before_accessing_storage() -> None:
    service = NotificationOutboxService(
        cast(async_sessionmaker[AsyncSession], object()),
        Settings(_env_file=None, runtime_governance_enabled=False),
    )

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await service.claim_ready(org_id="acme", limit=1, worker_id="worker")

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await service.drain_ready(org_id="acme", limit=1)


@pytest.mark.asyncio
async def test_frozen_runtime_control_delivery_rejects_before_storage() -> None:
    class NeverStorage:
        @property
        def session_factory(self) -> Any:
            raise AssertionError("storage must not be accessed while governance is frozen")

    auth = AuthContext(
        subject="runtime",
        roles=frozenset({"operator"}),
        org_ids=frozenset({"acme"}),
        auth_enabled=True,
        requested_org_id="acme",
        authentication_method="api_key",
    )
    request = RuntimeControlClaimRequest(
        org_id="acme",
        deployment_id=uuid4(),
        session_id=uuid4(),
        runtime_instance_id="runtime-1",
    )

    with pytest.raises(HTTPException) as exc:
        await claim_controls(
            request,
            cast(Any, NeverStorage()),
            Settings(_env_file=None, runtime_governance_enabled=False),
            auth,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "runtime_governance_disabled"

    acknowledgement = RuntimeControlAckRequest(
        org_id="acme",
        runtime_instance_id="runtime-1",
        lease_token="x" * 43,
        acknowledgement_id="ack-frozen",
        outcome="applied",
    )
    with pytest.raises(HTTPException) as exc:
        await acknowledge_control(
            uuid4(),
            acknowledgement,
            cast(Any, NeverStorage()),
            Settings(_env_file=None, runtime_governance_enabled=False),
            auth,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "runtime_governance_disabled"


@pytest.mark.asyncio
async def test_direct_services_reject_governance_effects_before_storage() -> None:
    db = cast(AsyncSession, object())

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await evaluate_budget_policies(db, "acme", execute_actions=True)

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await claim_runtime_controls(
            db,
            org_id="acme",
            deployment_id=uuid4(),
            session_id=uuid4(),
            runtime_instance_id="runtime-1",
            actor_subject="runtime",
            actor_roles=["operator"],
            lease_seconds=60,
            max_delivery_attempts=3,
            max_items=10,
        )

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await acknowledge_runtime_control(
            db,
            control_id=uuid4(),
            org_id="acme",
            runtime_instance_id="runtime-1",
            lease_token="x" * 43,
            acknowledgement_id="ack-frozen",
            outcome="applied",
            details={},
            actor_subject="runtime",
            actor_roles=["operator"],
        )


@pytest.mark.asyncio
async def test_side_effecting_api_requests_reject_before_storage() -> None:
    class NeverStorage:
        @property
        def session_factory(self) -> Any:
            raise AssertionError("storage must not be accessed while governance is frozen")

    storage = cast(Any, NeverStorage())
    settings = Settings(_env_file=None, runtime_governance_enabled=False)
    auth = AuthContext(
        subject="operator",
        roles=frozenset({"operator", "admin"}),
        org_ids=frozenset({"acme"}),
        auth_enabled=True,
        requested_org_id="acme",
        authentication_method="api_key",
    )
    request = cast(Any, object())
    now = datetime.now(UTC)
    calls = (
        lambda: ingest_action_batch(
            ActionBatchRequest(session_id=uuid4(), events=[]),
            storage,
            settings,
            auth,
            True,
        ),
        lambda: evaluate_policies(
            PolicyEvaluationRequest(org_id="acme", execute_actions=True),
            storage,
            settings,
            auth,
            request,
        ),
        lambda: run_detectors(
            DetectorRunRequest(org_id="acme", notify=True),
            storage,
            settings,
            auth,
            request,
        ),
        lambda: run_operations_cycle(
            RuntimeOperationsRunRequest(org_id="acme", execute_policy_actions=True),
            storage,
            settings,
            auth,
            request,
        ),
        lambda: export_siem_events(
            SiemExportRequest(
                org_id="acme",
                **{"from": now - timedelta(minutes=1), "to": now},
                dry_run=False,
                notification_targets=["https://hooks.example.com/events"],
            ),
            storage,
            settings,
            auth,
            request,
        ),
    )

    for call in calls:
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 409
        assert exc.value.detail == "runtime_governance_disabled"


def test_repository_surfaces_are_internal_and_manual_only() -> None:
    project = tomllib.loads((ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    workflow = (
        ROOT_DIR / ".github" / "workflows" / "production-gates.yml"
    ).read_text(encoding="utf-8")
    workflow_header = workflow.split("\njobs:", 1)[0]
    deployment = (ROOT_DIR / "deploy" / "k8s" / "api-deployment.yaml").read_text(
        encoding="utf-8"
    )
    disposition = (ROOT_DIR / "docs" / "DISPOSITION.md").read_text(encoding="utf-8")

    assert "internal" in project["description"].lower()
    assert "Private :: Do Not Upload" in project["classifiers"]
    assert "ai-trace-preflight" not in project.get("scripts", {})
    assert re.search(r"^  workflow_dispatch:\s*$", workflow_header, re.MULTILINE)
    for trigger in (
        "schedule",
        "push",
        "pull_request",
        "pull_request_target",
        "workflow_run",
        "workflow_call",
        "repository_dispatch",
        "release",
        "deployment",
    ):
        assert not re.search(rf"^  {trigger}:\s*$", workflow_header, re.MULTILINE)
    assert "production_evidence=false" in workflow
    assert "image: ai-trace-internal:manual" in deployment
    assert "imagePullPolicy: Never" in deployment
    assert 'name: RUNTIME_GOVERNANCE_ENABLED' in deployment
    assert 'name: OBSERVABILITY_SCHEDULER_ENABLED' in deployment
    assert deployment.count('value: "false"') >= 5
    assert "two independent active products" in disposition.lower()
    assert (
        "any runtime, deployment, provider, or customer integration exists"
        in disposition.lower()
    )
