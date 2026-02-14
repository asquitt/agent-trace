"""Tests for observability operations scheduler lifecycle behavior."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Settings
from src.services.operations_scheduler import ObservabilityOperationsScheduler


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "observability_scheduler_enabled": False,
        "observability_scheduler_org_ids": [],
        "observability_scheduler_interval_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def _session_factory_stub() -> async_sessionmaker:
    return cast(async_sessionmaker, object())


@pytest.mark.asyncio
async def test_scheduler_does_not_start_when_disabled() -> None:
    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(observability_scheduler_enabled=False),
    )

    await scheduler.start()
    status = scheduler.status()

    assert status["enabled"] is False
    assert status["running"] is False
    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_does_not_start_without_org_targets() -> None:
    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(observability_scheduler_enabled=True, observability_scheduler_org_ids=[]),
    )

    await scheduler.start()
    status = scheduler.status()

    assert status["enabled"] is True
    assert status["running"] is False
    await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_start_stop_updates_running_state(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def fake_run_loop(self: ObservabilityOperationsScheduler) -> None:
        started.set()
        await self._stop.wait()  # noqa: SLF001 - test-specific introspection

    monkeypatch.setattr(ObservabilityOperationsScheduler, "_run_loop", fake_run_loop)

    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(observability_scheduler_enabled=True, observability_scheduler_org_ids=["acme"]),
    )

    await scheduler.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    assert scheduler.status()["running"] is True

    await scheduler.stop()
    assert scheduler.status()["running"] is False
