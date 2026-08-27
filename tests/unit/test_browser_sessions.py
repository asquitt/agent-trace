"""Focused contracts for opaque browser-session credentials."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings
from src.models.auth import BrowserSession
from src.services.browser_sessions import (
    BrowserSessionAuthenticationError,
    BrowserSessionCsrfError,
    authenticate_browser_session,
    browser_csrf_is_valid,
    create_browser_session,
    hash_browser_secret,
)
from src.utils.time import utc_now_naive


class _ScalarResult:
    def __init__(self, row: BrowserSession | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> BrowserSession | None:
        return self.row


class _FakeSession:
    def __init__(self, row: BrowserSession | None = None) -> None:
        self.row = row
        self.added: BrowserSession | None = None
        self.commits = 0

    def add(self, row: BrowserSession) -> None:
        self.added = row

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, row: BrowserSession) -> None:
        if row.id is None:
            row.id = uuid4()

    async def execute(self, _query: Any) -> _ScalarResult:
        return _ScalarResult(self.row)


def _as_async_session(session: _FakeSession) -> AsyncSession:
    return cast(AsyncSession, cast(object, session))


def _row(*, csrf_token: str = "csrf-secret") -> BrowserSession:
    return BrowserSession(
        id=uuid4(),
        token_hash=hash_browser_secret("browser-secret"),
        csrf_token_hash=hash_browser_secret(csrf_token),
        subject="operator@example.com",
        roles=["operator", "viewer"],
        org_ids=["acme"],
        expires_at=utc_now_naive() + timedelta(minutes=30),
    )


def test_browser_csrf_requires_matching_cookie_header_and_stored_digest() -> None:
    digest = hash_browser_secret("csrf-secret")

    assert browser_csrf_is_valid(
        stored_hash=digest,
        cookie_token="csrf-secret",
        header_token="csrf-secret",
    )
    assert not browser_csrf_is_valid(
        stored_hash=digest,
        cookie_token="csrf-secret",
        header_token="wrong",
    )
    assert not browser_csrf_is_valid(
        stored_hash=digest,
        cookie_token=None,
        header_token=None,
    )


@pytest.mark.asyncio
async def test_create_browser_session_persists_only_secret_digests() -> None:
    fake = _FakeSession()
    created = await create_browser_session(
        _as_async_session(fake),
        subject="operator@example.com",
        roles=frozenset({"operator", "viewer"}),
        org_ids=frozenset({"acme"}),
        settings=Settings(browser_session_ttl_minutes=30),
    )

    assert fake.commits == 1
    assert fake.added is created.row
    assert created.row.token_hash == hash_browser_secret(created.token)
    assert created.row.csrf_token_hash == hash_browser_secret(created.csrf_token)
    assert created.token != created.row.token_hash
    assert created.csrf_token != created.row.csrf_token_hash
    assert created.row.roles == ["operator", "viewer"]
    assert created.row.org_ids == ["acme"]


@pytest.mark.asyncio
async def test_authenticate_browser_session_returns_snapshotted_principal() -> None:
    row = _row()
    auth = await authenticate_browser_session(
        _as_async_session(_FakeSession(row)),
        token="browser-secret",
        requested_org_id="acme",
        require_csrf=True,
        csrf_cookie="csrf-secret",
        csrf_header="csrf-secret",
    )

    assert auth.subject == "operator@example.com"
    assert auth.roles == frozenset({"operator", "viewer"})
    assert auth.org_ids == frozenset({"acme"})
    assert auth.authentication_method == "browser_session"
    assert auth.browser_session_id == row.id


@pytest.mark.asyncio
async def test_authenticate_browser_session_rejects_bad_csrf_and_expiry() -> None:
    row = _row()
    with pytest.raises(BrowserSessionCsrfError):
        await authenticate_browser_session(
            _as_async_session(_FakeSession(row)),
            token="browser-secret",
            requested_org_id="acme",
            require_csrf=True,
            csrf_cookie="csrf-secret",
            csrf_header="wrong",
        )

    row.expires_at = utc_now_naive() - timedelta(seconds=1)
    with pytest.raises(BrowserSessionAuthenticationError):
        await authenticate_browser_session(
            _as_async_session(_FakeSession(row)),
            token="browser-secret",
            requested_org_id="acme",
            require_csrf=False,
            csrf_cookie=None,
            csrf_header=None,
        )


def test_browser_session_model_has_no_raw_secret_columns() -> None:
    columns = set(BrowserSession.__table__.columns.keys())
    assert {"token_hash", "csrf_token_hash"}.issubset(columns)
    assert not columns.intersection({"token", "csrf_token", "api_key"})
