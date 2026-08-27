"""Runtime-control identity and API authorization contracts."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.routers.runtime_controls import _require_runtime_auth
from src.security import AuthContext
from src.services.runtime_controls import (
    _canonical_payload_hash,
    runtime_control_idempotency_key,
)


def test_runtime_control_idempotency_key_is_stable_per_policy_window() -> None:
    policy_id = uuid4()
    session_id = uuid4()
    window = datetime(2026, 8, 27)
    updated = datetime(2026, 8, 26, 20, 0)
    values = {
        "policy_id": policy_id,
        "session_id": session_id,
        "action_type": "throttle",
        "period_window_start": window,
        "policy_updated_at": updated,
        "throttle_rate": 0.5,
        "approval_id": None,
    }

    assert runtime_control_idempotency_key(**values) == runtime_control_idempotency_key(**values)
    assert runtime_control_idempotency_key(**values) != runtime_control_idempotency_key(
        **{**values, "period_window_start": window + timedelta(days=1)}
    )
    assert runtime_control_idempotency_key(**values) != runtime_control_idempotency_key(
        **{**values, "policy_updated_at": updated + timedelta(seconds=1)}
    )


def test_acknowledgement_hash_is_canonical() -> None:
    assert _canonical_payload_hash(
        outcome="applied",
        details={"rate": 0.5, "nested": {"a": 1, "b": 2}},
    ) == _canonical_payload_hash(
        outcome="applied",
        details={"nested": {"b": 2, "a": 1}, "rate": 0.5},
    )


@pytest.mark.parametrize(
    ("auth", "status_code"),
    [
        (
            AuthContext(
                subject="browser",
                roles=frozenset({"operator"}),
                org_ids=frozenset({"acme"}),
                auth_enabled=True,
                authentication_method="browser_session",
            ),
            403,
        ),
        (
            AuthContext(
                subject="viewer",
                roles=frozenset({"viewer"}),
                org_ids=frozenset({"acme"}),
                auth_enabled=True,
            ),
            403,
        ),
        (
            AuthContext(
                subject="other-org",
                roles=frozenset({"operator"}),
                org_ids=frozenset({"contoso"}),
                auth_enabled=True,
                requested_org_id="acme",
            ),
            403,
        ),
    ],
)
def test_runtime_control_api_rejects_non_runtime_authority(
    auth: AuthContext,
    status_code: int,
) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _require_runtime_auth(auth, "acme")
    assert exc_info.value.status_code == status_code


def test_runtime_control_api_accepts_org_operator_api_key() -> None:
    auth = AuthContext(
        subject="runtime",
        roles=frozenset({"operator"}),
        org_ids=frozenset({"acme"}),
        auth_enabled=True,
        requested_org_id="acme",
    )
    _require_runtime_auth(auth, "acme")
