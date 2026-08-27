"""Unit tests for observability runtime helpers."""

from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.observability import (
    AgentAction,
    AgentSession,
    BudgetPeriodType,
    BudgetPolicy,
    BudgetScopeType,
    PolicyActionType,
    PolicyStatus,
    SessionStatus,
)
from src.services import observability_runtime
from src.services.observability_runtime import (
    _action_priority,
    _apply_policy_action,
    _find_cycles,
    _period_window_start,
    _severity_rank,
    ensure_naive_utc,
)


def test_ensure_naive_utc_converts_timezone_aware() -> None:
    aware = datetime(2026, 2, 14, 12, 0, tzinfo=timezone.utc)
    normalized = ensure_naive_utc(aware)
    assert normalized is not None
    assert normalized.tzinfo is None
    assert normalized == datetime(2026, 2, 14, 12, 0)


def test_period_window_start_respects_period_type() -> None:
    as_of = datetime(2026, 2, 14, 16, 43, 29)

    assert _period_window_start(BudgetPeriodType.HOUR, as_of) == datetime(2026, 2, 14, 16, 0, 0)
    assert _period_window_start(BudgetPeriodType.DAY, as_of) == datetime(2026, 2, 14, 0, 0, 0)
    assert _period_window_start(BudgetPeriodType.MONTH, as_of) == datetime(2026, 2, 1, 0, 0, 0)


def test_find_cycles_detects_back_edges() -> None:
    a = uuid4()
    b = uuid4()
    c = uuid4()
    d = uuid4()
    edges = [(a, b), (b, c), (c, a), (c, d)]

    cycles = _find_cycles(edges)
    assert len(cycles) >= 1
    first_cycle = cycles[0]
    assert first_cycle[0] == first_cycle[-1]
    assert set(first_cycle[:-1]) == {a, b, c}


def test_action_priority_orders_enforcement() -> None:
    assert _action_priority("alert") < _action_priority("throttle")
    assert _action_priority("throttle") < _action_priority("require_approval")
    assert _action_priority("require_approval") < _action_priority("shutdown")


def test_severity_rank_orders_levels() -> None:
    assert _severity_rank("low") < _severity_rank("medium")
    assert _severity_rank("medium") < _severity_rank("high")
    assert _severity_rank("high") < _severity_rank("critical")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected_name"),
    [
        (PolicyActionType.THROTTLE, "policy_throttle_requested"),
        (PolicyActionType.SHUTDOWN, "policy_shutdown_requested"),
    ],
)
async def test_policy_controls_are_persisted_as_unconfirmed_requests(
    monkeypatch: pytest.MonkeyPatch,
    action: PolicyActionType,
    expected_name: str,
) -> None:
    now = datetime(2026, 8, 12, 12, 0)
    session = AgentSession(
        id=uuid4(),
        deployment_id=uuid4(),
        agent_id="agent-1",
        status=SessionStatus.ACTIVE,
        started_at=now - timedelta(minutes=10),
        session_metadata={},
    )
    policy = BudgetPolicy(
        id=uuid4(),
        org_id="acme",
        policy_name="request-only control",
        scope_type=BudgetScopeType.ORG,
        period_type=BudgetPeriodType.DAY,
        action_on_breach=action,
        status=PolicyStatus.ACTIVE,
        notification_targets=[],
        policy_metadata={},
    )

    async def fake_find_target_sessions(
        _db: AsyncSession,
        target_policy: BudgetPolicy,
        org_id: str,
    ) -> list[AgentSession]:
        assert target_policy is policy
        assert org_id == "acme"
        return [session]

    class FakeDb:
        def __init__(self) -> None:
            self.added: list[Any] = []

        def add(self, row: Any) -> None:
            self.added.append(row)

    fake_db = FakeDb()
    monkeypatch.setattr(
        observability_runtime,
        "_find_target_sessions",
        fake_find_target_sessions,
    )

    result = await _apply_policy_action(
        cast(AsyncSession, fake_db),
        policy,
        "acme",
        now,
        [{"trigger_type": "max_cost_usd", "observed_value": 2.0}],
        execute_actions=True,
        require_shutdown_approval=False,
        approval_max_age_minutes=60,
    )

    assert result["status"] == "requested"
    assert result["execution_confirmed"] is False
    assert result["delivery_status"] == "pending_runtime_adapter"
    assert session.status == SessionStatus.ACTIVE
    assert session.ended_at is None
    assert session.session_metadata is not None
    control = session.session_metadata["control"]
    assert control["state"] == "requested"
    assert control["requested_action"] == action.value
    assert control["execution_confirmed"] is False

    assert len(fake_db.added) == 1
    request_event = fake_db.added[0]
    assert isinstance(request_event, AgentAction)
    assert request_event.action_name == expected_name
    assert request_event.action_metadata is not None
    assert request_event.action_metadata["request_status"] == "persisted"
    assert request_event.action_metadata["execution_confirmed"] is False
