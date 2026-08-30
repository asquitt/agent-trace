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
from src.services import notifications as notification_service
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
async def test_frozen_direct_notification_helpers_reject_before_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_delivery(**_kwargs: Any) -> tuple[bool, str | None]:
        raise AssertionError("delivery must not start while governance is frozen")

    monkeypatch.setattr(notification_service, "_post_json_with_retry", fail_delivery)

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await notification_service.send_runtime_notifications(
            ["https://hooks.example.com/events"],
            {"event_type": "frozen"},
            slack_webhooks=[],
            pagerduty_routing_keys=[],
        )

    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await notification_service.send_webhook_notifications(
            ["https://hooks.example.com/events"],
            {"event_type": "frozen"},
        )


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
    env_example = (ROOT_DIR / ".env.example").read_text(encoding="utf-8")
    entrypoint = (ROOT_DIR / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    disposition = (ROOT_DIR / "docs" / "DISPOSITION.md").read_text(encoding="utf-8")
    readme = (ROOT_DIR / "README.md").read_text(encoding="utf-8")
    agent_instructions = (ROOT_DIR / "AGENTS.md").read_text(encoding="utf-8")
    claude_instructions = (ROOT_DIR / "CLAUDE.md").read_text(encoding="utf-8")
    pull_request_template = (
        ROOT_DIR / ".github" / "pull_request_template.md"
    ).read_text(encoding="utf-8")
    normalized_disposition = " ".join(disposition.lower().split())
    normalized_readme = " ".join(readme.lower().split())
    normalized_template = " ".join(pull_request_template.lower().split())

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
    for setting_name in (
        "TRACE_CAPTURE_PROMPTS",
        "PROVIDER_EXECUTION_ENABLED",
        "RUNTIME_GOVERNANCE_ENABLED",
        "OBSERVABILITY_SCHEDULER_ENABLED",
        "OBSERVABILITY_SCHEDULER_RUN_DETECTORS",
        "OBSERVABILITY_SCHEDULER_RUN_POLICIES",
        "OBSERVABILITY_SCHEDULER_EXECUTE_POLICY_ACTIONS",
        "OBSERVABILITY_SCHEDULER_ENABLE_NOTIFICATIONS",
        "MIGRATE_ON_START",
    ):
        literal = re.search(
            rf"- name: {re.escape(setting_name)}\s+value: \"([^\"]+)\"",
            deployment,
        )
        assert literal is not None, f"missing literal deployment default for {setting_name}"
        assert literal.group(1) == "false"
    for setting_name in (
        "PROVIDER_EXECUTION_ENABLED",
        "TRACE_CAPTURE_PROMPTS",
        "MIGRATE_ON_START",
        "RUNTIME_GOVERNANCE_ENABLED",
        "OBSERVABILITY_SCHEDULER_ENABLED",
    ):
        env_literals = [
            line.split("=", 1)[1].strip()
            for line in env_example.splitlines()
            if line.startswith(f"{setting_name}=")
        ]
        assert env_literals == ["false"]
    assert 'parse_boolean "MIGRATE_ON_START" "false"' in entrypoint
    assert 'if [ "$migrate_on_start" = "true" ]' in entrypoint
    assert "two independent active products" in normalized_disposition
    assert (
        "any runtime, deployment, provider, or customer integration exists"
        in normalized_disposition
    )
    assert "bounded repository maintenance may be explicitly authorized" in normalized_disposition
    assert "historical database contains no sensitive data" in normalized_disposition
    assert "historical stores contain no sensitive data" in normalized_readme
    for sensitive_trace_clause in (
        "deterministic safe error codes and exception types",
        "generic 500 without re-logging the original exception",
        "retains structural and numeric fields",
        "redacting model-derived descriptions, contexts, results, and explanations",
        "organization predicate before prompt-bearing spans are loaded",
        "sensitive error, and model-derived reasoning details",
    ):
        assert sensitive_trace_clause in normalized_disposition
        assert sensitive_trace_clause in normalized_readme
    for rollback_clause in (
        "remove or revoke provider credentials",
        "block provider egress",
        "stop or drain existing processes and in-flight calls",
        "verify that no provider request occurs",
    ):
        assert rollback_clause in normalized_disposition
        assert rollback_clause in normalized_readme
    assert "provider_execution_enabled=false" in normalized_disposition
    assert "older revision that does not implement the gate" in normalized_disposition
    assert "provider_execution_enabled=false" in normalized_readme
    assert "older revision that does not implement the gate" in normalized_readme
    assert "provider-gate rollback" in normalized_template
    assert "older revisions may ignore `provider_execution_enabled=false`" in normalized_template
    for rollback_term in (
        "credentials removed/revoked",
        "egress blocked",
        "processes and in-flight calls drained",
        "zero provider requests verified",
    ):
        assert rollback_term in normalized_template
    assert "do not extend the generic console, control plane" in normalized_readme
    assert (
        "implements the smallest required capability in its own canonical layer"
        in normalized_readme
    )
    for control_surface in (agent_instructions, claude_instructions):
        normalized = control_surface.lower()
        assert "binding product disposition" in normalized
        assert "read-only source and test evaluation" in normalized
        assert (
            "repository maintenance is limited to governance, dependency/security, "
            "or risk-reducing corrections that add no capability, compatibility promise, "
            "deployment path, or product-adoption behavior"
        ) in normalized
        assert "every capability expansion or adoption change must identify a named active product" in normalized
        assert "the first consumer implements the needed behavior in that product's canonical layer" in normalized
        assert "shared extraction is prohibited until two independent active products" in normalized
        assert "do not publish or deploy this repository" in normalized
        assert "verification does not authorize deployment, provider execution, adoption, or capability expansion" in normalized
        for prohibited_instruction in (
            "docker compose up",
            "alembic upgrade head",
            "uvicorn src.api.main:app",
            "for production ai agent fleets",
            "real-time monitoring, anomaly detection",
        ):
            assert prohibited_instruction not in normalized

    assert "disposition and ownership" in normalized_template
    assert "maintenance proof that this adds no capability" in normalized_template
    assert "required for every capability expansion or adoption" in normalized_template
    assert "named active product and accountable owner" in normalized_template
    assert "product-local implementation location" in normalized_template
    assert "two independent active products" in normalized_template
    assert "all eight second-consumer gate items" in normalized_template
