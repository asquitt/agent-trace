"""Unit tests for API authentication and authz helpers."""

from __future__ import annotations

from collections.abc import Mapping

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.config import Settings
from src.security import (
    authenticate_request,
    require_global_admin,
    require_org_access,
    require_roles,
)


def _make_request(headers: Mapping[str, str] | None = None) -> Request:
    pairs = []
    for key, value in (headers or {}).items():
        pairs.append((key.lower().encode("latin-1"), value.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": pairs,
    }
    return Request(scope)


def test_authenticate_request_disabled_mode_allows_local_admin() -> None:
    settings = Settings(api_auth_enabled=False)
    auth = authenticate_request(_make_request(), settings)
    assert auth.subject == "local-development"
    assert auth.is_global_admin


def test_authenticate_request_with_valid_api_key() -> None:
    settings = Settings(
        api_auth_enabled=True,
        api_keys=["topsecret:platform-bot:viewer|operator:acme|contoso"],
    )
    request = _make_request({"X-API-Key": "topsecret", "X-Org-Id": "acme"})
    auth = authenticate_request(request, settings)
    assert auth.subject == "platform-bot"
    assert "operator" in auth.roles
    assert auth.requested_org_id == "acme"


def test_authenticate_request_rejects_missing_key() -> None:
    settings = Settings(api_auth_enabled=True, api_keys=["k:s:viewer:acme"])
    with pytest.raises(HTTPException) as exc:
        authenticate_request(_make_request(), settings)
    assert exc.value.status_code == 401


def test_require_roles_and_org_access_enforced() -> None:
    settings = Settings(api_auth_enabled=True, api_keys=["k:s:viewer:acme"])
    auth = authenticate_request(_make_request({"X-API-Key": "k", "X-Org-Id": "acme"}), settings)

    require_roles(auth, "viewer")
    require_org_access(auth, "acme")

    with pytest.raises(HTTPException) as role_exc:
        require_roles(auth, "admin")
    assert role_exc.value.status_code == 403

    with pytest.raises(HTTPException) as org_exc:
        require_org_access(auth, "contoso")
    assert org_exc.value.status_code == 403


def test_global_admin_requires_admin_role_and_global_scope() -> None:
    settings = Settings(
        api_auth_enabled=True,
        api_keys=[
            "global:root:admin:*",
            "tenant-admin:owner:admin:acme",
            "global-viewer:reader:viewer:*",
        ],
    )

    global_admin = authenticate_request(
        _make_request({"X-API-Key": "global", "X-Org-Id": "acme"}),
        settings,
    )
    require_global_admin(global_admin)

    for token in ("tenant-admin", "global-viewer"):
        auth = authenticate_request(
            _make_request({"X-API-Key": token, "X-Org-Id": "acme"}),
            settings,
        )
        with pytest.raises(HTTPException) as exc:
            require_global_admin(auth)
        assert exc.value.status_code == 403
