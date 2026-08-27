"""Tests for observability operations scheduler lifecycle behavior."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from src.config import Settings
from src.services.operations_scheduler import (
    ObservabilityOperationsScheduler,
    PostgresAdvisoryLeadershipLock,
    SchedulerDurableFence,
    SchedulerFenceLost,
    SchedulerLeadershipLock,
)


class FakeLeadershipCoordinator:
    """In-memory stand-in for one PostgreSQL advisory lock."""

    def __init__(self) -> None:
        self.owner: FakeLeadershipLock | None = None


class FakeLeadershipLock:
    def __init__(self, coordinator: FakeLeadershipCoordinator | None = None) -> None:
        self.coordinator = coordinator or FakeLeadershipCoordinator()
        self.release_calls = 0
        self.verify_error: Exception | None = None
        self.acquire_enabled = True

    async def try_acquire(self) -> bool:
        if not self.acquire_enabled:
            return False
        if self.coordinator.owner in (None, self):
            self.coordinator.owner = self
            return True
        return False

    async def verify(self) -> bool:
        if self.verify_error is not None:
            raise self.verify_error
        return self.coordinator.owner is self

    async def release(self) -> None:
        self.release_calls += 1
        if self.coordinator.owner is self:
            self.coordinator.owner = None


class FakeDurableFenceCoordinator:
    """Shared lease state with a monotonic fencing token."""

    def __init__(self) -> None:
        self.owner: FakeDurableFence | None = None
        self.token = 0


class FakeDurableFence:
    def __init__(self, coordinator: FakeDurableFenceCoordinator | None = None) -> None:
        self.coordinator = coordinator or FakeDurableFenceCoordinator()
        self.token: int | None = None
        self.renew_enabled = True
        self.release_calls = 0

    async def acquire(self) -> bool:
        if self.coordinator.owner not in (None, self):
            return False
        self.coordinator.token += 1
        self.token = self.coordinator.token
        self.coordinator.owner = self
        return True

    async def renew(self) -> bool:
        return (
            self.renew_enabled
            and self.coordinator.owner is self
            and self.token == self.coordinator.token
        )

    @asynccontextmanager
    async def transaction(self, session: Any) -> Any:
        if not await self.renew():
            await session.rollback()
            raise SchedulerFenceLost("fake scheduler fence lost")
        try:
            yield
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    async def commit(self, session: Any) -> None:
        async with self.transaction(session):
            pass

    async def release(self) -> None:
        self.release_calls += 1
        if self.coordinator.owner is self and self.token == self.coordinator.token:
            self.coordinator.owner = None
        self.token = None


class FakeScalarResult:
    def __init__(self, value: bool) -> None:
        self.value = value

    def scalar_one(self) -> bool:
        return self.value


class FakeAdvisoryConnection:
    def __init__(self, results: list[bool]) -> None:
        self.results = results
        self.statements: list[str] = []
        self.commits = 0
        self.closed = False
        self.invalidated = False
        self.returned_reusable = False
        self.invalidation_causes: list[BaseException | None] = []
        self.events: list[str] = []
        self.unlock_error: BaseException | None = None
        self.execute_error: BaseException | None = None
        self.commit_error: BaseException | None = None
        self.unlock_started: asyncio.Event | None = None
        self.unlock_continue: asyncio.Event | None = None

    async def execute(self, statement: Any, parameters: Any = None) -> FakeScalarResult:
        del parameters
        statement_text = str(statement)
        self.statements.append(statement_text)
        if "pg_advisory_unlock" in statement_text:
            if self.unlock_started is not None:
                self.unlock_started.set()
            if self.unlock_continue is not None:
                await self.unlock_continue.wait()
            if self.unlock_error is not None:
                raise self.unlock_error
        if self.execute_error is not None:
            raise self.execute_error
        value = self.results.pop(0) if self.results else True
        return FakeScalarResult(value)

    async def commit(self) -> None:
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1

    async def close(self) -> None:
        self.events.append("close")
        self.returned_reusable = not self.invalidated
        self.closed = True

    async def invalidate(self, exception: BaseException | None = None) -> None:
        self.events.append("invalidate")
        self.invalidated = True
        self.invalidation_causes.append(exception)


class FakeAdvisoryEngine:
    def __init__(self, connection: FakeAdvisoryConnection) -> None:
        self.connection = connection
        self.connect_calls = 0

    async def connect(self) -> FakeAdvisoryConnection:
        self.connect_calls += 1
        return self.connection


class FakeRunSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self) -> FakeRunSession:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeRunSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[FakeRunSession] = []

    def __call__(self) -> FakeRunSession:
        session = FakeRunSession()
        self.sessions.append(session)
        return session


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


def _leadership_lock_stub() -> SchedulerLeadershipLock:
    return FakeLeadershipLock()


def _durable_fence_stub() -> SchedulerDurableFence:
    return FakeDurableFence()


@pytest.mark.asyncio
async def test_postgres_advisory_lock_holds_connection_until_release() -> None:
    connection = FakeAdvisoryConnection([True, True, True])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is True
    assert connection.closed is False
    assert await leadership_lock.verify() is True

    await leadership_lock.release()

    assert connection.closed is True
    assert connection.invalidated is False
    assert connection.returned_reusable is True
    assert connection.commits == 3
    assert connection.statements == [
        "SELECT pg_try_advisory_lock(:lock_id)",
        "SELECT 1",
        "SELECT pg_advisory_unlock(:lock_id)",
    ]


@pytest.mark.asyncio
async def test_postgres_advisory_lock_closes_standby_connection() -> None:
    connection = FakeAdvisoryConnection([False])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is False
    assert connection.closed is True


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_on_unlock_failure() -> None:
    connection = FakeAdvisoryConnection([True])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is True
    connection.unlock_error = RuntimeError("unlock failed")

    await leadership_lock.release()

    assert connection.invalidated is True
    assert connection.closed is True
    assert connection.returned_reusable is False
    assert isinstance(connection.invalidation_causes[0], RuntimeError)
    assert connection.events[-2:] == ["invalidate", "close"]


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_when_release_is_cancelled() -> None:
    connection = FakeAdvisoryConnection([True])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is True
    connection.unlock_started = asyncio.Event()
    connection.unlock_continue = asyncio.Event()
    release_task = asyncio.create_task(leadership_lock.release())
    await asyncio.wait_for(connection.unlock_started.wait(), timeout=1)

    release_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await release_task

    assert connection.invalidated is True
    assert connection.closed is True
    assert connection.returned_reusable is False
    assert isinstance(connection.invalidation_causes[0], asyncio.CancelledError)
    assert connection.events[-2:] == ["invalidate", "close"]


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_when_unlock_reports_not_held() -> None:
    connection = FakeAdvisoryConnection([True, False])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is True

    await leadership_lock.release()

    assert connection.invalidated is True
    assert connection.returned_reusable is False
    assert isinstance(connection.invalidation_causes[0], RuntimeError)


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_uncertain_acquire_failure() -> None:
    connection = FakeAdvisoryConnection([])
    connection.execute_error = ConnectionError("acquire result unknown")
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    with pytest.raises(ConnectionError):
        await leadership_lock.try_acquire()

    assert connection.invalidated is True
    assert connection.returned_reusable is False
    assert isinstance(connection.invalidation_causes[0], ConnectionError)


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_acquired_commit_failure() -> None:
    connection = FakeAdvisoryConnection([True])
    connection.commit_error = ConnectionError("acquired commit failed")
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    with pytest.raises(ConnectionError):
        await leadership_lock.try_acquire()

    assert connection.invalidated is True
    assert connection.returned_reusable is False


@pytest.mark.asyncio
async def test_postgres_advisory_lock_closes_confirmed_standby_on_commit_failure() -> None:
    connection = FakeAdvisoryConnection([False])
    connection.commit_error = ConnectionError("standby transaction failed")
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    with pytest.raises(ConnectionError):
        await leadership_lock.try_acquire()

    assert connection.invalidated is False
    assert connection.returned_reusable is True


@pytest.mark.asyncio
async def test_postgres_advisory_lock_invalidates_verification_failure() -> None:
    connection = FakeAdvisoryConnection([True])
    engine = cast(AsyncEngine, FakeAdvisoryEngine(connection))
    leadership_lock = PostgresAdvisoryLeadershipLock(engine)

    assert await leadership_lock.try_acquire() is True
    connection.execute_error = ConnectionError("verification failed")

    with pytest.raises(ConnectionError):
        await leadership_lock.verify()

    assert connection.invalidated is True
    assert connection.returned_reusable is False


@pytest.mark.asyncio
async def test_scheduler_does_not_start_when_disabled() -> None:
    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(observability_scheduler_enabled=False),
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
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
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
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
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
    )

    await scheduler.start()
    await asyncio.wait_for(started.wait(), timeout=1)
    assert scheduler.status()["running"] is True

    await scheduler.stop()
    assert scheduler.status()["running"] is False


@pytest.mark.asyncio
async def test_scheduler_continues_when_one_org_run_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    bad_org = "bad-org"
    good_org = "good-org"

    async def fake_run_once(self: ObservabilityOperationsScheduler, org_id: str) -> dict[str, Any]:
        calls.append(org_id)
        if org_id == bad_org:
            raise RuntimeError("simulated org failure")
        self._stop.set()  # noqa: SLF001 - test-specific introspection
        return {"org_id": org_id}

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", fake_run_once)

    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(observability_scheduler_enabled=True, observability_scheduler_org_ids=[bad_org, good_org]),
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
    )

    await scheduler.start()
    await asyncio.wait_for(scheduler._stop.wait(), timeout=1)  # noqa: SLF001
    await scheduler.stop()

    status = scheduler.status()
    assert calls == [bad_org, good_org]
    assert status["last_error"] == "1 org run(s) failed"
    assert status["failed_orgs"] == 1
    assert status["org_count"] == 2
    assert bad_org not in repr(status)
    assert good_org not in repr(status)


@pytest.mark.asyncio
async def test_scheduler_drains_backlog_after_current_run_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drain_calls: list[str] = []

    class FakeOutbox:
        async def enqueue_scheduler_run(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("patched run_once owns this test")

        async def drain_ready(self, *, org_id: str, limit: int) -> Any:
            assert limit == 25
            drain_calls.append(org_id)
            scheduler._stop.set()  # noqa: SLF001 - test-specific loop boundary
            from src.services.notification_outbox import DrainSummary

            return DrainSummary()

        async def cleanup_terminal_deliveries(self, *, org_id: str, limit: int) -> int:
            assert org_id == "acme"
            assert limit == 25
            return 0

        async def durable_failure_count(self, *, org_id: str) -> int:
            assert org_id == "acme"
            return 2

    async def failing_run_once(
        _self: ObservabilityOperationsScheduler,
        _org_id: str,
    ) -> dict[str, Any]:
        raise RuntimeError("current producer failed")

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", failing_run_once)
    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(
            observability_scheduler_enabled=True,
            observability_scheduler_org_ids=["acme"],
            observability_scheduler_enable_notifications=True,
        ),
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
        notification_outbox=cast(Any, FakeOutbox()),
    )

    await scheduler.start()
    await asyncio.wait_for(scheduler._stop.wait(), timeout=1)  # noqa: SLF001
    await scheduler.stop()

    assert drain_calls == ["acme"]
    assert scheduler.status()["failed_orgs"] == 1
    assert scheduler.status()["notification_delivery_failures"] == 2


@pytest.mark.asyncio
async def test_active_leader_maintains_historical_outbox_when_delivery_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    maintenance_calls: list[tuple[str, int]] = []

    class FakeOutbox:
        async def enqueue_scheduler_run(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("notifications are disabled")

        async def drain_ready(self, **_kwargs: Any) -> Any:
            raise AssertionError("delivery is disabled")

        async def cleanup_terminal_deliveries(self, *, org_id: str, limit: int) -> int:
            maintenance_calls.append((org_id, limit))
            return 1

        async def durable_failure_count(self, *, org_id: str) -> int:
            assert org_id == "acme"
            return 1

    async def run_once(
        _self: ObservabilityOperationsScheduler,
        org_id: str,
    ) -> dict[str, Any]:
        assert org_id == "acme"
        scheduler._stop.set()  # noqa: SLF001
        return {"org_id": org_id}

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", run_once)
    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(
            observability_scheduler_enabled=True,
            observability_scheduler_org_ids=["acme"],
            observability_scheduler_enable_notifications=False,
        ),
        leadership_lock=_leadership_lock_stub(),
        durable_fence=_durable_fence_stub(),
        notification_outbox=cast(Any, FakeOutbox()),
    )

    await scheduler.start()
    await asyncio.wait_for(scheduler._stop.wait(), timeout=1)  # noqa: SLF001
    await scheduler.stop()

    assert maintenance_calls == [("acme", 25)]
    assert scheduler.status()["notification_delivery_failures"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("notifications_requested", [False, True])
async def test_scheduler_stages_notification_result_without_network_io(
    monkeypatch: pytest.MonkeyPatch,
    notifications_requested: bool,
) -> None:
    async def forbidden_dispatch(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("scheduler must not perform outbound notification I/O")

    monkeypatch.setattr(
        "src.services.notifications.send_runtime_notifications",
        forbidden_dispatch,
    )
    session_factory = FakeRunSessionFactory()
    durable_fence = FakeDurableFence()
    assert await durable_fence.acquire() is True
    class FakeOutbox:
        enqueue_calls = 0

        async def enqueue_scheduler_run(self, _session: Any, **_kwargs: Any) -> dict[str, Any]:
            self.enqueue_calls += 1
            return {
                "queued": 1,
                "accepted": 0,
                "delivery_semantics": "endpoint_acceptance",
            }

        async def drain_ready(self, **_kwargs: Any) -> Any:
            raise AssertionError("run_once must not drain the outbox")

    outbox = FakeOutbox()
    scheduler = ObservabilityOperationsScheduler(
        cast(async_sessionmaker, session_factory),
        _settings(
            observability_scheduler_run_detectors=False,
            observability_scheduler_run_policies=False,
            observability_scheduler_enable_notifications=notifications_requested,
        ),
        leadership_lock=_leadership_lock_stub(),
        durable_fence=durable_fence,
        notification_outbox=cast(Any, outbox),
    )
    finished: dict[str, Any] = {}

    async def fake_start_run_row(org_id: str) -> str:
        assert org_id == "acme"
        return "run-1"

    async def fake_complete_run(_session: Any, run_id: str, **kwargs: Any) -> None:
        finished.update({"run_id": run_id, **kwargs})

    monkeypatch.setattr(scheduler, "_start_run_row", fake_start_run_row)
    monkeypatch.setattr(scheduler, "_complete_run_in_session", fake_complete_run)

    result = await scheduler.run_once("acme")

    notification_result = result["notification_result"]
    if notifications_requested:
        assert notification_result == {
            "queued": 1,
            "accepted": 0,
            "delivery_semantics": "endpoint_acceptance",
        }
        assert outbox.enqueue_calls == 1
    else:
        assert notification_result["attempted"] == 0
        assert notification_result["skipped"] is True
        assert notification_result["skip_reason"] == "scheduler_notifications_disabled"
        assert outbox.enqueue_calls == 0
    assert finished["notification_summary"] == notification_result
    assert scheduler._state["org_runs"]["acme"]["last_notification_result"] == (  # noqa: SLF001
        notification_result
    )
    assert session_factory.sessions[0].commits == 1


@pytest.mark.asyncio
async def test_only_advisory_lock_leader_runs_scheduler_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = FakeLeadershipCoordinator()
    durable_coordinator = FakeDurableFenceCoordinator()
    first_lock = FakeLeadershipLock(coordinator)
    second_lock = FakeLeadershipLock(coordinator)
    cycle_started = asyncio.Event()
    calls: list[ObservabilityOperationsScheduler] = []

    async def fake_run_once(
        self: ObservabilityOperationsScheduler,
        org_id: str,
    ) -> dict[str, Any]:
        calls.append(self)
        cycle_started.set()
        return {"org_id": org_id}

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", fake_run_once)

    settings = _settings(
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
    )
    first = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=first_lock,
        durable_fence=FakeDurableFence(durable_coordinator),
    )
    second = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=second_lock,
        durable_fence=FakeDurableFence(durable_coordinator),
    )

    await asyncio.gather(first.start(), second.start())
    await asyncio.wait_for(cycle_started.wait(), timeout=1)
    for _ in range(20):
        leadership_states = {
            first.status()["leadership_state"],
            second.status()["leadership_state"],
        }
        if leadership_states == {"leader", "standby"}:
            break
        await asyncio.sleep(0)

    assert len(calls) == 1
    assert calls[0].status()["is_leader"] is True
    assert leadership_states == {"leader", "standby"}

    await asyncio.gather(first.stop(), second.stop())


@pytest.mark.asyncio
async def test_standby_takes_over_after_leader_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = FakeLeadershipCoordinator()
    durable_coordinator = FakeDurableFenceCoordinator()
    first_lock = FakeLeadershipLock(coordinator)
    second_lock = FakeLeadershipLock(coordinator)
    first_cycle = asyncio.Event()
    second_cycle = asyncio.Event()
    calls: list[ObservabilityOperationsScheduler] = []

    async def fake_run_once(
        self: ObservabilityOperationsScheduler,
        org_id: str,
    ) -> dict[str, Any]:
        calls.append(self)
        if self is first:
            first_cycle.set()
        else:
            second_cycle.set()
        return {"org_id": org_id}

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", fake_run_once)
    monkeypatch.setattr("src.services.operations_scheduler._LEADERSHIP_RETRY_SECONDS", 0.01)

    settings = _settings(
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
    )
    first = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=first_lock,
        durable_fence=FakeDurableFence(durable_coordinator),
    )
    second = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=second_lock,
        durable_fence=FakeDurableFence(durable_coordinator),
    )

    await first.start()
    await asyncio.wait_for(first_cycle.wait(), timeout=1)
    await second.start()
    for _ in range(20):
        if second.status()["leadership_state"] == "standby":
            break
        await asyncio.sleep(0)

    await first.stop()
    await asyncio.wait_for(second_cycle.wait(), timeout=1)

    assert calls == [first, second]
    assert second.status()["is_leader"] is True
    assert coordinator.owner is second_lock

    await second.stop()


@pytest.mark.asyncio
async def test_durable_fence_prevents_overlap_after_both_advisory_connections_are_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leadership_coordinator = FakeLeadershipCoordinator()
    execution_coordinator = FakeLeadershipCoordinator()
    durable_coordinator = FakeDurableFenceCoordinator()
    first_leadership = FakeLeadershipLock(leadership_coordinator)
    second_leadership = FakeLeadershipLock(leadership_coordinator)
    first_execution = FakeLeadershipLock(execution_coordinator)
    second_execution = FakeLeadershipLock(execution_coordinator)
    first_run_started = asyncio.Event()
    allow_first_to_finish = asyncio.Event()
    second_run_started = asyncio.Event()
    active_runs = 0
    max_active_runs = 0

    async def fake_run_once(
        self: ObservabilityOperationsScheduler,
        org_id: str,
    ) -> dict[str, Any]:
        nonlocal active_runs, max_active_runs
        active_runs += 1
        max_active_runs = max(max_active_runs, active_runs)
        try:
            if self is first:
                first_run_started.set()
                await allow_first_to_finish.wait()
            else:
                second_run_started.set()
            return {"org_id": org_id}
        finally:
            active_runs -= 1

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", fake_run_once)
    monkeypatch.setattr("src.services.operations_scheduler._LEADERSHIP_RETRY_SECONDS", 0.01)

    settings = _settings(
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
    )
    first = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=first_leadership,
        execution_lock=first_execution,
        durable_fence=FakeDurableFence(durable_coordinator),
    )
    second = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        settings,
        leadership_lock=second_leadership,
        execution_lock=second_execution,
        durable_fence=FakeDurableFence(durable_coordinator),
    )

    await first.start()
    await asyncio.wait_for(first_run_started.wait(), timeout=1)

    # Model a common-mode database connection reset destroying both advisory
    # sessions while the first replica is still inside its run. The durable
    # lease remains owned, so the replacement cannot overlap the old process.
    leadership_coordinator.owner = None
    execution_coordinator.owner = None
    first_leadership.acquire_enabled = False
    first_execution.acquire_enabled = False
    await second.start()
    for _ in range(50):
        if second.status()["leadership_state"] == "standby":
            break
        await asyncio.sleep(0)

    assert second.status()["is_leader"] is False
    assert durable_coordinator.owner is first._durable_fence  # noqa: SLF001
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(second_run_started.wait(), timeout=0.05)

    allow_first_to_finish.set()
    await asyncio.wait_for(second_run_started.wait(), timeout=1)

    assert max_active_runs == 1
    assert second.status()["is_leader"] is True
    assert execution_coordinator.owner is second_execution

    await asyncio.gather(first.stop(), second.stop())


@pytest.mark.asyncio
async def test_leadership_connection_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leadership_lock = FakeLeadershipLock()
    cycle_started = asyncio.Event()
    calls: list[str] = []

    async def fake_run_once(
        _self: ObservabilityOperationsScheduler,
        org_id: str,
    ) -> dict[str, Any]:
        calls.append(org_id)
        leadership_lock.verify_error = ConnectionError("connection lost")
        cycle_started.set()
        return {"org_id": org_id}

    monkeypatch.setattr(ObservabilityOperationsScheduler, "run_once", fake_run_once)
    monkeypatch.setattr("src.services.operations_scheduler._LEADERSHIP_RETRY_SECONDS", 0.01)

    scheduler = ObservabilityOperationsScheduler(
        _session_factory_stub(),
        _settings(
            observability_scheduler_enabled=True,
            observability_scheduler_org_ids=["first", "must-not-run"],
        ),
        leadership_lock=leadership_lock,
        durable_fence=_durable_fence_stub(),
    )

    await scheduler.start()
    await asyncio.wait_for(cycle_started.wait(), timeout=1)
    for _ in range(20):
        if scheduler.status()["leadership_state"] == "error":
            break
        await asyncio.sleep(0)

    status = scheduler.status()
    assert calls == ["first"]
    assert status["is_leader"] is False
    assert status["leadership_state"] == "error"
    assert status["last_leadership_error"] == "leadership_lock_unavailable"
    assert "connection lost" not in repr(status)

    await scheduler.stop()
