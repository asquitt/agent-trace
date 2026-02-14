"""Unit tests for observability runtime helpers."""

from datetime import datetime, timezone
from uuid import uuid4

from src.models.observability import BudgetPeriodType
from src.services.observability_runtime import (
    _action_priority,
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
