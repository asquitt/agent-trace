"""Fail-closed contracts for notification outbox maintenance."""

from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.services.notification_outbox import NotificationOutboxService
from src.services.runtime_governance import RuntimeGovernanceDisabledError


class _NeverSessionFactory:
    def __call__(self) -> Any:
        raise AssertionError("storage must not be accessed while governance is frozen")


class _ReadOnlySession:
    def __init__(self) -> None:
        self.rolled_back = False

    async def __aenter__(self) -> _ReadOnlySession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _statement: object) -> int:
        return 3

    async def rollback(self) -> None:
        self.rolled_back = True


class _ReadOnlySessionFactory:
    def __init__(self, session: _ReadOnlySession) -> None:
        self.session = session

    def __call__(self) -> _ReadOnlySession:
        return self.session


def _service(session_factory: object) -> NotificationOutboxService:
    return NotificationOutboxService(
        cast(async_sessionmaker[AsyncSession], session_factory),
        Settings(_env_file=None, runtime_governance_enabled=False),
    )


@pytest.mark.asyncio
async def test_frozen_recovery_rejects_before_storage_access() -> None:
    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await _service(_NeverSessionFactory()).recover_expired_claims(
            org_id="acme",
            limit=10,
        )


@pytest.mark.asyncio
async def test_frozen_cleanup_rejects_before_storage_access() -> None:
    with pytest.raises(RuntimeGovernanceDisabledError, match="runtime_governance_disabled"):
        await _service(_NeverSessionFactory()).cleanup_terminal_deliveries(
            org_id="acme",
            limit=10,
        )


@pytest.mark.asyncio
async def test_frozen_durable_failure_count_remains_read_only_available() -> None:
    session = _ReadOnlySession()

    count = await _service(_ReadOnlySessionFactory(session)).durable_failure_count(org_id="acme")

    assert count == 3
    assert session.rolled_back is True
