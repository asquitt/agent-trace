"""Unit tests for observability router helper utilities."""

from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routers.observability import (
    _default_group_update_match_statuses,
    _parse_anomaly_group_fingerprint,
)
from src.models.observability import AnomalyStatus, AnomalyType


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
