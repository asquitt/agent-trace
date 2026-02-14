"""Background scheduler for continuous observability runtime operations."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..config import Settings
from ..models.observability import ObservabilityOperationRun, SystemAuditEvent
from .notifications import collect_policy_notification_targets, send_webhook_notifications
from .observability_runtime import (
    DetectorConfig,
    evaluate_budget_policies,
    run_anomaly_detectors,
)

logger = structlog.get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ObservabilityOperationsScheduler:
    """Periodic scheduler for detectors and policy enforcement."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = {
            "running": False,
            "last_tick_at": None,
            "last_success_at": None,
            "last_error": None,
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
        self._state["running"] = True
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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._state["running"] = False
        logger.info("observability_scheduler_stopped")

    def status(self) -> dict[str, Any]:
        """Get scheduler runtime status."""
        state = dict(self._state)
        state["interval_seconds"] = self._settings.observability_scheduler_interval_seconds
        state["enabled"] = self._settings.observability_scheduler_enabled
        state["org_ids"] = list(self._settings.observability_scheduler_org_ids)
        return state

    async def _start_run_row(self, org_id: str) -> str:
        async with self._session_factory() as session:
            row = ObservabilityOperationRun(
                org_id=org_id,
                run_type="scheduler",
                started_at=_utcnow_naive(),
                success=False,
                run_metadata={
                    "source": "scheduler",
                    "run_detectors": self._settings.observability_scheduler_run_detectors,
                    "run_policies": self._settings.observability_scheduler_run_policies,
                    "execute_policy_actions": self._settings.observability_scheduler_execute_policy_actions,
                    "notifications_enabled": self._settings.observability_scheduler_enable_notifications,
                },
            )
            session.add(row)
            await session.commit()
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
        error_message: Optional[str] = None,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(ObservabilityOperationRun, run_id)
            if row is None:
                return
            row.completed_at = _utcnow_naive()
            row.success = success
            row.error_message = error_message
            row.detector_summary = detector_summary
            row.policy_summary = policy_summary
            row.notification_summary = notification_summary
            session.add(
                SystemAuditEvent(
                    occurred_at=_utcnow_naive(),
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
            await session.commit()

    async def run_once(self, org_id: str) -> dict[str, Any]:
        """Run one detectors + policy loop for a single org."""
        run_started = _utcnow_iso()
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
        )

        try:
            async with self._session_factory() as session:
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
                        execute_actions=self._settings.observability_scheduler_execute_policy_actions,
                        require_shutdown_approval=self._settings.observability_shutdown_requires_approval,
                        approval_max_age_minutes=(
                            self._settings.observability_shutdown_approval_max_age_minutes
                        ),
                    )
                await session.commit()

            if self._settings.observability_scheduler_enable_notifications:
                targets = set(self._settings.observability_notification_webhooks)
                for target in collect_policy_notification_targets(policy_summary):
                    targets.add(target)
                notification_payload = {
                    "event_type": "observability_scheduler_run",
                    "org_id": org_id,
                    "run_started_at": run_started,
                    "detector_summary": detector_summary,
                    "policy_summary": policy_summary,
                }
                notification_result = await send_webhook_notifications(
                    sorted(targets),
                    notification_payload,
                    timeout_seconds=self._settings.observability_notification_timeout_seconds,
                    max_attempts=self._settings.observability_notification_max_attempts,
                    retry_backoff_seconds=self._settings.observability_notification_retry_backoff_seconds,
                )
            await self._finish_run_row(
                run_id,
                org_id=org_id,
                success=True,
                detector_summary=detector_summary,
                policy_summary=policy_summary,
                notification_summary=notification_result,
            )
        except Exception as exc:
            await self._finish_run_row(
                run_id,
                org_id=org_id,
                success=False,
                detector_summary=detector_summary,
                policy_summary=policy_summary,
                notification_summary=notification_result,
                error_message=str(exc),
            )
            raise

        org_state = self._state["org_runs"].setdefault(org_id, {})
        org_state["last_started_at"] = run_started
        org_state["last_completed_at"] = _utcnow_iso()
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
        while not self._stop.is_set():
            self._state["last_tick_at"] = _utcnow_iso()
            try:
                for org_id in self._settings.observability_scheduler_org_ids:
                    await self.run_once(org_id)
                self._state["last_success_at"] = _utcnow_iso()
                self._state["last_error"] = None
            except Exception as exc:  # pragma: no cover - runtime infrastructure dependent
                self._state["last_error"] = str(exc)
                logger.exception("observability_scheduler_tick_failed", error=str(exc))

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self._settings.observability_scheduler_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
