"""Background scheduler for continuous observability runtime operations."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any, AsyncContextManager, AsyncIterator, Protocol
from uuid import uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from ..config import Settings
from ..models.observability import ObservabilityOperationRun, SystemAuditEvent
from ..utils.time import utc_now_iso, utc_now_naive
from .notifications import (
    runtime_event_severity,
    skipped_notification_result,
)
from .observability_runtime import (
    DetectorConfig,
    evaluate_budget_policies,
    run_anomaly_detectors,
)

logger = structlog.get_logger(__name__)

# A stable, application-specific signed bigint used for the PostgreSQL session lock.
# Keeping the lock global to this database guarantees one scheduler leader across
# every API replica without requiring a coordination table or schema migration.
_SCHEDULER_ADVISORY_LOCK_ID = 0x4149545241434501
# A second advisory lock reduces ordinary leadership churn. Both advisory sessions
# share one database failure domain, so correctness comes from the durable lease and
# its monotonic token below rather than assuming either connection survives failover.
_SCHEDULER_EXECUTION_LOCK_ID = 0x4149545241434502
_SCHEDULER_LEASE_NAME = "observability-operations"
_LEADERSHIP_RETRY_SECONDS = 5.0

_ACQUIRE_DURABLE_LEASE = text(
    """
    INSERT INTO observability_scheduler_leases (
        lease_name,
        owner_id,
        fence_token,
        lease_expires_at,
        heartbeat_at,
        created_at,
        updated_at
    ) VALUES (
        :lease_name,
        :owner_id,
        1,
        timezone('utc', clock_timestamp()) + make_interval(secs => :lease_seconds),
        timezone('utc', clock_timestamp()),
        timezone('utc', clock_timestamp()),
        timezone('utc', clock_timestamp())
    )
    ON CONFLICT (lease_name) DO UPDATE
    SET owner_id = EXCLUDED.owner_id,
        fence_token = observability_scheduler_leases.fence_token + 1,
        lease_expires_at = EXCLUDED.lease_expires_at,
        heartbeat_at = EXCLUDED.heartbeat_at,
        updated_at = EXCLUDED.updated_at
    WHERE observability_scheduler_leases.lease_expires_at
          <= timezone('utc', clock_timestamp())
       OR observability_scheduler_leases.owner_id = EXCLUDED.owner_id
    RETURNING fence_token
    """
)
_RENEW_UNEXPIRED_DURABLE_LEASE = text(
    """
    UPDATE observability_scheduler_leases
    SET lease_expires_at = timezone('utc', clock_timestamp())
                           + make_interval(secs => :lease_seconds),
        heartbeat_at = timezone('utc', clock_timestamp()),
        updated_at = timezone('utc', clock_timestamp())
    WHERE lease_name = :lease_name
      AND owner_id = :owner_id
      AND fence_token = :fence_token
      AND lease_expires_at > timezone('utc', clock_timestamp())
    RETURNING fence_token
    """
)
_RENEW_LOCKED_DURABLE_LEASE = text(
    """
    UPDATE observability_scheduler_leases
    SET lease_expires_at = timezone('utc', clock_timestamp())
                           + make_interval(secs => :lease_seconds),
        heartbeat_at = timezone('utc', clock_timestamp()),
        updated_at = timezone('utc', clock_timestamp())
    WHERE lease_name = :lease_name
      AND owner_id = :owner_id
      AND fence_token = :fence_token
    RETURNING fence_token
    """
)
_RELEASE_DURABLE_LEASE = text(
    """
    UPDATE observability_scheduler_leases
    SET lease_expires_at = timezone('utc', clock_timestamp()),
        heartbeat_at = timezone('utc', clock_timestamp()),
        updated_at = timezone('utc', clock_timestamp())
    WHERE lease_name = :lease_name
      AND owner_id = :owner_id
      AND fence_token = :fence_token
    """
)

_PUBLIC_SCHEDULER_STATUS_KEYS = (
    "enabled",
    "running",
    "is_leader",
    "leadership_state",
    "last_leadership_change_at",
    "last_leadership_error",
    "last_tick_at",
    "last_success_at",
    "last_error",
    "interval_seconds",
    "org_count",
    "failed_orgs",
)


def public_scheduler_status(scheduler: Any) -> dict[str, Any]:
    """Return aggregate scheduler health without tenant identifiers."""
    if scheduler is None:
        return {
            "enabled": False,
            "running": False,
            "is_leader": False,
            "leadership_state": "disabled",
            "last_leadership_change_at": None,
            "last_leadership_error": None,
            "last_tick_at": None,
            "last_success_at": None,
            "last_error": None,
            "interval_seconds": None,
            "org_count": 0,
            "failed_orgs": 0,
            "health": "disabled",
            "reason": "scheduler_not_initialized",
        }

    raw = scheduler.status()
    result = {key: raw.get(key) for key in _PUBLIC_SCHEDULER_STATUS_KEYS}
    result["org_count"] = int(result.get("org_count") or 0)
    result["failed_orgs"] = int(result.get("failed_orgs") or 0)
    if not result.get("enabled"):
        health = "disabled"
    elif not result.get("running"):
        health = "stopped"
    elif result.get("leadership_state") == "standby":
        health = "standby"
    elif result.get("leadership_state") == "contending":
        health = "contending"
    elif result.get("leadership_state") == "error":
        health = "error"
    elif (
        result.get("leadership_state") != "leader"
        or not result.get("is_leader")
        or result["failed_orgs"] > 0
    ):
        health = "degraded"
    else:
        health = "healthy"
    result["health"] = health
    return result


class SchedulerLeadershipLock(Protocol):
    """Coordination contract used by the scheduler and deterministic tests."""

    async def try_acquire(self) -> bool:
        """Attempt to become leader without blocking."""
        ...

    async def verify(self) -> bool:
        """Return whether the held leadership connection is still usable."""
        ...

    async def release(self) -> None:
        """Release leadership and its dedicated connection."""
        ...


class SchedulerFenceLost(RuntimeError):
    """Raised when a scheduler write no longer owns its durable fence token."""


class SchedulerDurableFence(Protocol):
    """Durable lease contract used to fence scheduler-owned database commits."""

    async def acquire(self) -> bool:
        """Acquire an expired lease and a new monotonic fencing token."""
        ...

    async def renew(self) -> bool:
        """Renew the currently held unexpired lease."""
        ...

    def transaction(self, session: AsyncSession) -> AsyncContextManager[None]:
        """Fence every write in a transaction behind the exact current token."""
        ...

    async def commit(self, session: AsyncSession) -> None:
        """Fence and commit writes already staged in a transaction."""
        ...

    async def release(self) -> None:
        """Expire the currently held lease without changing its fencing token."""
        ...


class PostgresAdvisoryLeadershipLock:
    """Hold scheduler leadership on a dedicated PostgreSQL connection."""

    def __init__(self, engine: AsyncEngine, lock_id: int = _SCHEDULER_ADVISORY_LOCK_ID) -> None:
        self._engine = engine
        self._lock_id = lock_id
        self._connection: AsyncConnection | None = None

    async def try_acquire(self) -> bool:
        """Try to acquire the session lock, retaining the connection on success."""
        if self._connection is not None:
            return await self.verify()

        connection = await self._engine.connect()
        lock_result_known = False
        acquired = False
        try:
            result = await connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": self._lock_id},
            )
            acquired = bool(result.scalar_one())
            lock_result_known = True
            # End the implicit transaction. The session-level advisory lock remains
            # held because this AsyncConnection stays checked out until release().
            await connection.commit()
            if acquired:
                self._connection = connection
                return True
        except BaseException as exc:
            if lock_result_known and not acquired:
                await connection.close()
            else:
                await self._invalidate_connection(connection, exc)
            raise

        await connection.close()
        return False

    async def verify(self) -> bool:
        """Check that the dedicated connection holding the session lock is alive."""
        connection = self._connection
        if connection is None:
            return False
        try:
            await connection.execute(text("SELECT 1"))
            await connection.commit()
        except BaseException as exc:
            self._connection = None
            await self._invalidate_connection(connection, exc)
            raise
        return True

    async def release(self) -> None:
        """Unlock normally, or invalidate the DBAPI session when unlock is uncertain."""
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        try:
            result = await connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": self._lock_id},
            )
            released = bool(result.scalar_one())
            if not released:
                raise RuntimeError("scheduler advisory lock was not held by its connection")
            await connection.commit()
        except asyncio.CancelledError as exc:
            # Never let cancellation return a session that may still own a
            # session-level advisory lock to the pool.
            await self._invalidate_connection(connection, exc)
            raise
        except Exception as exc:
            await self._invalidate_connection(connection, exc)
            logger.exception("observability_scheduler_leadership_release_failed")
            return

        try:
            await connection.close()
        except asyncio.CancelledError as exc:
            # The unlock committed, but invalidate anyway rather than allowing
            # cancellation during close to leave ambiguous pooled state.
            await self._invalidate_connection(connection, exc)
            raise
        except Exception as exc:
            await self._invalidate_connection(connection, exc)
            logger.exception("observability_scheduler_leadership_close_failed")

    @staticmethod
    async def _invalidate_connection(
        connection: AsyncConnection,
        cause: BaseException,
    ) -> None:
        """Terminate an uncertain DBAPI session without returning it as reusable."""
        try:
            # Shield pool invalidation from the cancellation that caused cleanup.
            # SQLAlchemy discards the DBAPI connection even if termination reports
            # an error, so it cannot retain the advisory lock in the pool.
            await asyncio.shield(connection.invalidate(cause))
        except asyncio.CancelledError:
            # A second cancellation may interrupt this wait, but the shielded
            # invalidation continues and still prevents pool reuse.
            logger.warning("observability_scheduler_leadership_invalidation_cancelled")
            raise
        except Exception:
            # Do not call close() if invalidation itself could not begin: returning
            # an uncertain session to the pool would be less safe than leaking it.
            logger.exception("observability_scheduler_leadership_invalidation_failed")
            return

        # After invalidation, close only finalizes the facade; the DBAPI session
        # has already been terminated/discarded and cannot be pooled.
        with suppress(Exception):
            await asyncio.shield(connection.close())


class PostgresSchedulerDurableFence:
    """PostgreSQL lease whose token is checked in every scheduler write transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        lease_seconds: int,
        lease_name: str = _SCHEDULER_LEASE_NAME,
        owner_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._lease_seconds = lease_seconds
        self._lease_name = lease_name
        self._owner_id = owner_id or str(uuid4())
        self._fence_token: int | None = None
        self._local_transaction_lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Atomically take an absent, expired, or same-owner lease."""
        async with self._local_transaction_lock:
            async with self._session_factory() as session:
                result = await session.execute(_ACQUIRE_DURABLE_LEASE, self._parameters())
                token = result.scalar_one_or_none()
                if token is None:
                    await session.rollback()
                    self._fence_token = None
                    return False
                await session.commit()
        self._fence_token = int(token)
        return True

    async def renew(self) -> bool:
        """Renew only the exact, still-current owner/token pair."""
        token = self._fence_token
        if token is None:
            return False
        async with self._local_transaction_lock:
            async with self._session_factory() as session:
                result = await session.execute(
                    _RENEW_UNEXPIRED_DURABLE_LEASE,
                    self._parameters(token=token),
                )
                renewed_token = result.scalar_one_or_none()
                if renewed_token is None:
                    await session.rollback()
                    self._fence_token = None
                    return False
                await session.commit()
        return True

    @asynccontextmanager
    async def transaction(self, session: AsyncSession) -> AsyncIterator[None]:
        """Lock and renew the lease row before any scheduler mutation is executed."""
        async with self._local_transaction_lock:
            token = self._fence_token
            if token is None:
                await session.rollback()
                raise SchedulerFenceLost("scheduler durable fence is not held")
            try:
                result = await session.execute(
                    _RENEW_UNEXPIRED_DURABLE_LEASE,
                    self._parameters(token=token),
                )
                if result.scalar_one_or_none() is None:
                    self._fence_token = None
                    await session.rollback()
                    raise SchedulerFenceLost(
                        "scheduler durable fence token is stale or expired"
                    )

                # This transaction now owns the lease-row lock. A replacement
                # cannot acquire the lease while scheduler helpers query, flush,
                # or mutate their domain rows inside this block.
                yield

                result = await session.execute(
                    _RENEW_LOCKED_DURABLE_LEASE,
                    self._parameters(token=token),
                )
                if result.scalar_one_or_none() is None:
                    self._fence_token = None
                    raise SchedulerFenceLost("scheduler durable fence token changed")
                await session.commit()
            except BaseException:
                with suppress(Exception):
                    await session.rollback()
                raise

    async def commit(self, session: AsyncSession) -> None:
        """Fence and commit writes that a caller staged before entering the guard."""
        async with self.transaction(session):
            pass

    async def release(self) -> None:
        """Expire only this exact owner/token lease so a standby may take over."""
        token = self._fence_token
        self._fence_token = None
        if token is None:
            return
        async with self._local_transaction_lock:
            async with self._session_factory() as session:
                await session.execute(
                    _RELEASE_DURABLE_LEASE,
                    self._parameters(token=token),
                )
                await session.commit()

    def _parameters(self, *, token: int | None = None) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "lease_name": self._lease_name,
            "owner_id": self._owner_id,
            "lease_seconds": self._lease_seconds,
        }
        if token is not None:
            parameters["fence_token"] = token
        return parameters

class ObservabilityOperationsScheduler:
    """Periodic scheduler for detectors, policy evaluation, and control requests."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
        *,
        leadership_lock: SchedulerLeadershipLock | None = None,
        execution_lock: SchedulerLeadershipLock | None = None,
        durable_fence: SchedulerDurableFence | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        if leadership_lock is not None and execution_lock is None:
            # Explicit test coordinators may implement both roles with one
            # idempotent lock object. Production always receives two IDs.
            execution_lock = leadership_lock
        elif leadership_lock is None or execution_lock is None:
            bind = session_factory.kw.get("bind")
            if not isinstance(bind, AsyncEngine):
                raise TypeError("scheduler session factory must be bound to an AsyncEngine")
            if leadership_lock is None:
                leadership_lock = PostgresAdvisoryLeadershipLock(bind)
            if execution_lock is None:
                execution_lock = PostgresAdvisoryLeadershipLock(
                    bind,
                    lock_id=_SCHEDULER_EXECUTION_LOCK_ID,
                )
        self._leadership_lock = leadership_lock
        self._execution_lock = execution_lock
        self._durable_fence = durable_fence or PostgresSchedulerDurableFence(
            session_factory,
            lease_seconds=settings.observability_scheduler_lease_seconds,
        )
        self._task: asyncio.Task | None = None
        self._lease_heartbeat_task: asyncio.Task | None = None
        self._durable_fence_lost = asyncio.Event()
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = {
            "running": False,
            "is_leader": False,
            "leadership_state": (
                "stopped" if settings.observability_scheduler_enabled else "disabled"
            ),
            "last_leadership_change_at": None,
            "last_leadership_error": None,
            "last_tick_at": None,
            "last_success_at": None,
            "last_error": None,
            "last_tick_failures": [],
            "org_runs": {},
        }

    async def start(self) -> None:
        """Start scheduler loop if enabled."""
        if not self._settings.observability_scheduler_enabled:
            logger.info("observability_scheduler_disabled")
            return
        if not self._settings.observability_scheduler_org_ids:
            logger.warning("observability_scheduler_enabled_without_org_ids")
            return
        if self._task is not None and not self._task.done():
            return

        self._stop.clear()
        self._durable_fence_lost.clear()
        self._state["running"] = True
        self._set_leadership_state("contending")
        self._task = asyncio.create_task(self._run_loop(), name="observability-ops-scheduler")
        logger.info(
            "observability_scheduler_started",
            interval_seconds=self._settings.observability_scheduler_interval_seconds,
            org_count=len(self._settings.observability_scheduler_org_ids),
        )

    async def stop(self) -> None:
        """Stop scheduler loop if running."""
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        await self._release_coordination()
        self._task = None
        self._state["running"] = False
        self._set_leadership_state(
            "stopped" if self._settings.observability_scheduler_enabled else "disabled"
        )
        logger.info("observability_scheduler_stopped")

    def status(self) -> dict[str, Any]:
        """Get a public-safe scheduler status without tenant identifiers."""
        return {
            "enabled": self._settings.observability_scheduler_enabled,
            "running": self._state["running"],
            "is_leader": self._state["is_leader"],
            "leadership_state": self._state["leadership_state"],
            "last_leadership_change_at": self._state["last_leadership_change_at"],
            "last_leadership_error": self._state["last_leadership_error"],
            "last_tick_at": self._state["last_tick_at"],
            "last_success_at": self._state["last_success_at"],
            "last_error": self._state["last_error"],
            "interval_seconds": self._settings.observability_scheduler_interval_seconds,
            "org_count": len(self._settings.observability_scheduler_org_ids),
            "failed_orgs": len(self._state["last_tick_failures"]),
        }

    def _set_leadership_state(self, state: str, *, error: str | None = None) -> None:
        if self._state["leadership_state"] != state:
            self._state["last_leadership_change_at"] = utc_now_iso()
        self._state["leadership_state"] = state
        self._state["is_leader"] = state == "leader"
        self._state["last_leadership_error"] = error

    async def _ensure_leadership(self) -> bool:
        """Acquire or verify advisory coordination and the durable write fence."""
        try:
            if self._state["is_leader"]:
                if self._durable_fence_lost.is_set():
                    self._set_leadership_state("error", error="scheduler_fence_lost")
                    await self._release_coordination()
                    return False
                leadership_valid = await self._leadership_lock.verify()
                execution_valid = (
                    leadership_valid
                    if self._execution_lock is self._leadership_lock
                    else await self._execution_lock.verify()
                )
                durable_valid = await self._durable_fence.renew()
                if leadership_valid and execution_valid and durable_valid:
                    return True
                self._set_leadership_state("error", error="scheduler_fence_lost")
                await self._release_coordination()
                return False

            if not await self._leadership_lock.try_acquire():
                self._set_leadership_state("standby")
                return False

            if not await self._execution_lock.try_acquire():
                # Another replica is still completing an in-flight side-effect
                # window. Relinquish coordination so no replacement reports leader
                # until it owns both independent fences.
                self._set_leadership_state("standby")
                await self._leadership_lock.release()
                return False

            if not await self._durable_fence.acquire():
                # Both advisory sessions may have disappeared together while the
                # prior leader is still running. The durable lease stays owned and
                # prevents this contender from entering the write window.
                self._set_leadership_state("standby")
                await self._release_advisory_locks()
                return False

            self._durable_fence_lost.clear()
            self._start_lease_heartbeat()
            self._set_leadership_state("leader")
            logger.info("observability_scheduler_leadership_acquired")
            return True
        except Exception as exc:  # pragma: no cover - exact driver errors vary
            self._set_leadership_state("error", error="leadership_lock_unavailable")
            await self._release_coordination()
            logger.exception(
                "observability_scheduler_leadership_error",
                error=str(exc),
            )
            return False

    async def _release_coordination(self) -> None:
        """Expire the durable lease, then release both advisory coordinators."""
        await self._stop_lease_heartbeat()
        try:
            await self._durable_fence.release()
        except Exception:
            logger.exception("observability_scheduler_durable_fence_release_failed")
        await self._release_advisory_locks()

    async def _release_advisory_locks(self) -> None:
        """Release the execution advisory lock before the leadership lock."""
        if self._execution_lock is self._leadership_lock:
            await self._leadership_lock.release()
            return
        try:
            await self._execution_lock.release()
        finally:
            await self._leadership_lock.release()

    def _start_lease_heartbeat(self) -> None:
        """Continuously renew the durable lease during long scheduler work."""
        if self._lease_heartbeat_task is not None and not self._lease_heartbeat_task.done():
            return
        self._lease_heartbeat_task = asyncio.create_task(
            self._run_lease_heartbeat(),
            name="observability-scheduler-lease-heartbeat",
        )

    async def _stop_lease_heartbeat(self) -> None:
        task = self._lease_heartbeat_task
        self._lease_heartbeat_task = None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run_lease_heartbeat(self) -> None:
        interval = max(1.0, self._settings.observability_scheduler_lease_seconds / 3)
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=interval)
                    return
                except TimeoutError:
                    pass
                try:
                    if await self._durable_fence.renew():
                        continue
                except Exception as exc:  # pragma: no cover - driver-specific failures
                    logger.exception(
                        "observability_scheduler_durable_fence_heartbeat_failed",
                        error=str(exc),
                    )
                self._durable_fence_lost.set()
                self._set_leadership_state("error", error="scheduler_fence_lost")
                return
        except asyncio.CancelledError:
            raise

    @asynccontextmanager
    async def _fenced_transaction(self, session: AsyncSession) -> AsyncIterator[None]:
        """Run all scheduler mutations while the durable lease row is locked."""
        if self._durable_fence_lost.is_set():
            await session.rollback()
            raise SchedulerFenceLost("scheduler durable fence heartbeat was lost")
        async with self._durable_fence.transaction(session):
            yield

    async def _wait(self, timeout: float) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=timeout)

    async def _start_run_row(self, org_id: str) -> str:
        async with self._session_factory() as session:
            async with self._fenced_transaction(session):
                row = ObservabilityOperationRun(
                    org_id=org_id,
                    run_type="scheduler",
                    started_at=utc_now_naive(),
                    success=False,
                    run_metadata={
                        "source": "scheduler",
                        "run_detectors": self._settings.observability_scheduler_run_detectors,
                        "run_policies": self._settings.observability_scheduler_run_policies,
                        "execute_policy_actions": (
                            self._settings.observability_scheduler_execute_policy_actions
                        ),
                        "notifications_requested": (
                            self._settings.observability_scheduler_enable_notifications
                        ),
                        "notification_delivery_mode": (
                            "fail_closed_durable_outbox_required"
                        ),
                    },
                )
                session.add(row)
            await session.refresh(row)
            return str(row.id)

    async def _finish_run_row(
        self,
        run_id: str,
        *,
        org_id: str,
        success: bool,
        detector_summary: dict[str, Any],
        policy_summary: dict[str, Any],
        notification_summary: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            async with self._fenced_transaction(session):
                row = await session.get(ObservabilityOperationRun, run_id)
                if row is None:
                    return
                row.completed_at = utc_now_naive()
                row.success = success
                row.error_message = error_message
                row.detector_summary = detector_summary
                row.policy_summary = policy_summary
                row.notification_summary = notification_summary
                session.add(
                    SystemAuditEvent(
                        occurred_at=utc_now_naive(),
                        actor_subject="system:scheduler",
                        actor_roles=["system"],
                        org_id=org_id,
                        action="scheduler_run",
                        resource_type="observability_operation_run",
                        resource_id=str(row.id),
                        request_id=None,
                        success=success,
                        details={
                            "run_type": row.run_type,
                            "error_message": error_message,
                            "detector_summary": detector_summary,
                            "policy_summary": policy_summary,
                            "notification_summary": notification_summary,
                        },
                    )
                )

    async def run_once(self, org_id: str) -> dict[str, Any]:
        """Run one detectors + policy loop for a single org."""
        run_started = utc_now_iso()
        detector_summary: dict[str, Any] = {}
        policy_summary: dict[str, Any] = {}
        notification_result: dict[str, Any] = {}
        run_id = await self._start_run_row(org_id)

        detector_config = DetectorConfig(
            current_window_minutes=self._settings.observability_detector_current_window_minutes,
            baseline_window_hours=self._settings.observability_detector_baseline_window_hours,
            api_spike_multiplier=self._settings.observability_detector_api_spike_multiplier,
            cost_spike_multiplier=self._settings.observability_detector_cost_spike_multiplier,
            unusual_resource_min_calls=self._settings.observability_detector_unusual_resource_min_calls,
            memory_divergence_threshold=self._settings.observability_detector_memory_divergence_threshold,
            anomaly_dedupe_window_minutes=(
                self._settings.observability_detector_anomaly_dedupe_window_minutes
            ),
            anomaly_reopen_acknowledged=(
                self._settings.observability_detector_anomaly_reopen_acknowledged
            ),
        )

        try:
            async with self._session_factory() as session:
                async with self._fenced_transaction(session):
                    if self._settings.observability_scheduler_run_detectors:
                        detector_summary = await run_anomaly_detectors(
                            session,
                            org_id,
                            config=detector_config,
                        )
                    if self._settings.observability_scheduler_run_policies:
                        policy_summary = await evaluate_budget_policies(
                            session,
                            org_id,
                            execute_actions=(
                                self._settings.observability_scheduler_execute_policy_actions
                            ),
                            require_shutdown_approval=(
                                self._settings.observability_shutdown_requires_approval
                            ),
                            approval_max_age_minutes=(
                                self._settings.observability_shutdown_approval_max_age_minutes
                            ),
                        )

            notification_payload = {
                "event_type": "observability_scheduler_run",
                "org_id": org_id,
                "run_started_at": run_started,
                "detector_summary": detector_summary,
                "policy_summary": policy_summary,
            }
            event_severity = runtime_event_severity(notification_payload)
            min_severity = self._settings.observability_notification_min_severity
            notification_result = skipped_notification_result(
                min_severity=min_severity,
                max_attempts=self._settings.observability_notification_max_attempts,
                event_severity=event_severity,
                reason=(
                    "durable_outbox_required"
                    if self._settings.observability_scheduler_enable_notifications
                    else "scheduler_notifications_disabled"
                ),
            )
            await self._finish_run_row(
                run_id,
                org_id=org_id,
                success=True,
                detector_summary=detector_summary,
                policy_summary=policy_summary,
                notification_summary=notification_result,
            )
        except SchedulerFenceLost:
            # A stale leader must not write a completion/audit row with an old
            # fencing token. The durable start row remains incomplete evidence.
            raise
        except Exception as exc:
            try:
                await self._finish_run_row(
                    run_id,
                    org_id=org_id,
                    success=False,
                    detector_summary=detector_summary,
                    policy_summary=policy_summary,
                    notification_summary=notification_result,
                    error_message=str(exc),
                )
            except SchedulerFenceLost:
                raise SchedulerFenceLost(
                    "scheduler fence was lost while recording run failure"
                ) from exc
            raise

        org_state = self._state["org_runs"].setdefault(org_id, {})
        org_state["last_started_at"] = run_started
        org_state["last_completed_at"] = utc_now_iso()
        org_state["last_detector_summary"] = detector_summary
        org_state["last_policy_summary"] = policy_summary
        org_state["last_notification_result"] = notification_result

        return {
            "run_id": run_id,
            "org_id": org_id,
            "detector_summary": detector_summary,
            "policy_summary": policy_summary,
            "notification_result": notification_result,
        }

    async def _run_loop(self) -> None:
        try:
            while not self._stop.is_set():
                if not await self._ensure_leadership():
                    await self._wait(
                        min(
                            float(self._settings.observability_scheduler_interval_seconds),
                            _LEADERSHIP_RETRY_SECONDS,
                        )
                    )
                    continue

                self._state["last_tick_at"] = utc_now_iso()
                tick_failures: list[dict[str, str]] = []
                successful_runs = 0
                leadership_lost = False
                for org_id in self._settings.observability_scheduler_org_ids:
                    # Verify the dedicated lock connection between tenant runs. If it
                    # has failed, stop this cycle before another replica takes over.
                    if not await self._ensure_leadership():
                        leadership_lost = True
                        break
                    try:
                        await self.run_once(org_id)
                        successful_runs += 1
                        org_state = self._state["org_runs"].setdefault(org_id, {})
                        org_state["last_error"] = None
                    except Exception as exc:  # pragma: no cover - infrastructure dependent
                        error_message = str(exc)
                        tick_failures.append({"org_id": org_id, "error": error_message})
                        org_state = self._state["org_runs"].setdefault(org_id, {})
                        org_state["last_error"] = error_message
                        org_state["last_failed_at"] = utc_now_iso()
                        logger.exception(
                            "observability_scheduler_org_run_failed",
                            org_id=org_id,
                            error=error_message,
                        )
                    # Re-verify both fences immediately after the complete database
                    # and notification window. A replacement cannot enter while the
                    # execution fence is held, so failover never overlaps side effects.
                    if not await self._ensure_leadership():
                        leadership_lost = True
                        break

                if successful_runs > 0:
                    self._state["last_success_at"] = utc_now_iso()

                if leadership_lost:
                    self._state["last_error"] = "scheduler leadership lost during tick"
                elif tick_failures:
                    self._state["last_error"] = f"{len(tick_failures)} org run(s) failed"
                else:
                    self._state["last_error"] = None
                self._state["last_tick_failures"] = tick_failures

                wait_seconds = float(self._settings.observability_scheduler_interval_seconds)
                if leadership_lost:
                    wait_seconds = min(wait_seconds, _LEADERSHIP_RETRY_SECONDS)
                await self._wait(wait_seconds)
        finally:
            await self._release_coordination()
            if self._state["running"]:
                self._set_leadership_state("stopped")
