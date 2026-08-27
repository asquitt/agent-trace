"""Unit tests for observability router helper utilities."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from src.api.routers.observability import (
    _active_session_cutoff,
    _activity_future_cutoff,
    _default_group_update_match_statuses,
    _monotonic_activity_watermark,
    _parse_anomaly_group_fingerprint,
    _recent_active_session_filters,
    _stale_active_session_filters,
    _validate_activity_timestamp,
)
from src.models.observability import AgentSession, AnomalyStatus, AnomalyType


def test_parse_anomaly_group_fingerprint_with_deployment_scope() -> None:
    deployment_id = uuid4()
    fingerprint = f"api_spike:{deployment_id}:sudden api usage increase"
    anomaly_type, parsed_deployment, title_scope = _parse_anomaly_group_fingerprint(fingerprint)
    assert anomaly_type == AnomalyType.API_SPIKE
    assert parsed_deployment == deployment_id
    assert title_scope == "sudden api usage increase"


def test_parse_anomaly_group_fingerprint_with_none_deployment_scope() -> None:
    anomaly_type, deployment_id, title_scope = _parse_anomaly_group_fingerprint(
        "cost_spike:no-deploy:daily cost burst"
    )
    assert anomaly_type == AnomalyType.COST_SPIKE
    assert deployment_id is None
    assert title_scope == "daily cost burst"


def test_parse_anomaly_group_fingerprint_rejects_bad_format() -> None:
    with pytest.raises(HTTPException) as exc:
        _parse_anomaly_group_fingerprint("api_spike:missing-title")
    assert exc.value.status_code == 400


def test_default_group_update_match_statuses() -> None:
    assert _default_group_update_match_statuses(AnomalyStatus.ACKNOWLEDGED) == [AnomalyStatus.OPEN]
    assert _default_group_update_match_statuses(AnomalyStatus.OPEN) == [AnomalyStatus.ACKNOWLEDGED]
    assert _default_group_update_match_statuses(AnomalyStatus.RESOLVED) == [
        AnomalyStatus.OPEN,
        AnomalyStatus.ACKNOWLEDGED,
    ]


def test_active_session_cutoff_distinguishes_fresh_and_ancient_activity() -> None:
    now = datetime(2026, 8, 13, 12, 0, 0)
    cutoff = _active_session_cutoff(now, inactivity_minutes=30)

    assert now - timedelta(minutes=5) >= cutoff
    assert now - timedelta(hours=2) < cutoff
    assert cutoff == datetime(2026, 8, 13, 11, 30, 0)


def test_activity_watermark_never_moves_backward() -> None:
    started_at = datetime(2026, 8, 13, 11, 0, 0)
    current = datetime(2026, 8, 13, 11, 50, 0)

    assert _monotonic_activity_watermark(
        started_at=started_at,
        current=current,
        candidate=datetime(2026, 8, 13, 10, 0, 0),
    ) == current
    assert _monotonic_activity_watermark(
        started_at=started_at,
        current=current,
        candidate=datetime(2026, 8, 13, 12, 0, 0),
    ) == datetime(2026, 8, 13, 12, 0, 0)
    assert _monotonic_activity_watermark(
        started_at=started_at,
        current=None,
        candidate=datetime(2026, 8, 13, 10, 0, 0),
    ) == started_at


def test_activity_timestamp_allows_small_skew_and_rejects_untrusted_future_time() -> None:
    received_at = datetime(2026, 8, 27, 12, 0, 0)
    allowed = received_at + timedelta(minutes=5)

    assert _activity_future_cutoff(received_at) == allowed
    assert (
        _validate_activity_timestamp(
            allowed,
            received_at=received_at,
            field_name="occurred_at",
        )
        == allowed
    )
    with pytest.raises(HTTPException) as exc:
        _validate_activity_timestamp(
            allowed + timedelta(microseconds=1),
            received_at=received_at,
            field_name="occurred_at",
        )

    assert exc.value.status_code == 422
    assert "5 minutes in the future" in str(exc.value.detail)


def test_active_session_filters_use_coalesced_activity_timestamp() -> None:
    cutoff = datetime(2026, 8, 13, 11, 30, 0)
    future_cutoff = datetime(2026, 8, 13, 12, 5, 0)
    recent_sql = str(
        select(AgentSession.id)
        .where(*_recent_active_session_filters(cutoff, future_cutoff))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    stale_sql = str(
        select(AgentSession.id)
        .where(*_stale_active_session_filters(cutoff, future_cutoff))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    activity_expression = "coalesce(agent_sessions.last_activity_at, agent_sessions.started_at)"
    assert activity_expression in recent_sql
    assert activity_expression in stale_sql
    assert ">=" in recent_sql
    assert "<=" in recent_sql
    assert "<" in stale_sql
    assert ">" in stale_sql
