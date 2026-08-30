"""PostgreSQL integration coverage for the durable scheduler fence."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.config import Settings
from src.models import NotificationDelivery, ObservabilityOperationRun, SystemAuditEvent
from src.services.operations_scheduler import (
    ObservabilityOperationsScheduler,
    PostgresSchedulerDurableFence,
    SchedulerFenceLost,
)
from src.utils.time import utc_now_naive


@pytest.mark.asyncio
async def test_scheduler_fence_rejects_stale_writer_after_takeover() -> None:
    """A new token must roll back every write attempted by the old owner."""
    engine, session_factory = _isolated_database()
    lease_name = f"test-scheduler-{uuid4()}"
    stale_resource_id = f"stale-{uuid4()}"
    current_resource_id = f"current-{uuid4()}"
    first = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=30,
        lease_name=lease_name,
        owner_id=str(uuid4()),
    )
    second = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=30,
        lease_name=lease_name,
        owner_id=str(uuid4()),
    )

    try:
        assert await first.acquire() is True
        assert await second.acquire() is False
        async with session_factory() as session:
            first_token = int(
                (
                    await session.execute(
                        text(
                            "SELECT fence_token FROM observability_scheduler_leases "
                            "WHERE lease_name = :lease_name"
                        ),
                        {"lease_name": lease_name},
                    )
                ).scalar_one()
            )
            await session.execute(
                text(
                    "UPDATE observability_scheduler_leases "
                    "SET lease_expires_at = timezone('utc', clock_timestamp()) "
                    "- interval '1 second' WHERE lease_name = :lease_name"
                ),
                {"lease_name": lease_name},
            )
            await session.commit()

        assert await second.acquire() is True
        async with session_factory() as session:
            second_token = int(
                (
                    await session.execute(
                        text(
                            "SELECT fence_token FROM observability_scheduler_leases "
                            "WHERE lease_name = :lease_name"
                        ),
                        {"lease_name": lease_name},
                    )
                ).scalar_one()
            )
        assert second_token == first_token + 1

        async with session_factory() as stale_session:
            await _insert_audit_probe(stale_session, stale_resource_id)
            with pytest.raises(SchedulerFenceLost):
                await first.commit(stale_session)

        async with session_factory() as current_session:
            await _insert_audit_probe(current_session, current_resource_id)
            await second.commit(current_session)

        async with session_factory() as session:
            stale_count = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM system_audit_events "
                            "WHERE resource_id = :resource_id"
                        ),
                        {"resource_id": stale_resource_id},
                    )
                ).scalar_one()
            )
            current_count = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM system_audit_events "
                            "WHERE resource_id = :resource_id"
                        ),
                        {"resource_id": current_resource_id},
                    )
                ).scalar_one()
            )
        assert stale_count == 0
        assert current_count == 1
    finally:
        await second.release()
        await first.release()
        async with session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM system_audit_events "
                    "WHERE resource_id IN (:stale_resource_id, :current_resource_id)"
                ),
                {
                    "stale_resource_id": stale_resource_id,
                    "current_resource_id": current_resource_id,
                },
            )
            await session.execute(
                text(
                    "DELETE FROM observability_scheduler_leases "
                    "WHERE lease_name = :lease_name"
                ),
                {"lease_name": lease_name},
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_atomically_persists_notification_outbox() -> None:
    """Scheduler success commits its completion, audit, and secret-free outbox together."""
    engine, session_factory = _isolated_database()
    org_id = f"scheduler-outbox-{uuid4()}"
    lease_name = f"test-scheduler-{uuid4()}"
    durable_fence = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=30,
        lease_name=lease_name,
    )
    assert await durable_fence.acquire() is True
    scheduler = ObservabilityOperationsScheduler(
        session_factory,
        Settings(
            runtime_governance_enabled=True,
            observability_scheduler_run_detectors=False,
            observability_scheduler_run_policies=False,
            observability_scheduler_enable_notifications=True,
            observability_notification_only_on_actionable=False,
            observability_notification_min_severity="info",
            observability_notification_webhooks=["https://hooks.example.com/runtime-secret"],
            observability_notification_allowed_hosts=["hooks.example.com"],
            observability_notification_fingerprint_key="f" * 32,
        ),
        durable_fence=durable_fence,
    )

    try:
        result = await scheduler.run_once(org_id)
        notification_result = result["notification_result"]
        assert notification_result["queued"] == 1
        assert notification_result["accepted"] == 0
        assert notification_result["delivery_semantics"] == "endpoint_acceptance"

        async with session_factory() as session:
            run = (
                await session.execute(
                    select(ObservabilityOperationRun).where(
                        ObservabilityOperationRun.id == result["run_id"]
                    )
                )
            ).scalar_one()
            audit = (
                await session.execute(
                    select(SystemAuditEvent).where(
                        SystemAuditEvent.resource_id == result["run_id"],
                        SystemAuditEvent.action == "scheduler_run",
                    )
                )
            ).scalar_one()
            delivery = (
                await session.execute(
                    select(NotificationDelivery).where(
                        NotificationDelivery.operation_run_id == result["run_id"]
                    )
                )
            ).scalar_one()
        assert run.success is True
        assert run.run_metadata["notifications_requested"] is True
        assert run.run_metadata["notification_delivery_mode"] == (
            "durable_transactional_outbox"
        )
        assert run.notification_summary == notification_result
        assert audit.details["notification_summary"] == notification_result
        assert delivery.status == "pending"
        assert delivery.channel == "webhook"
        assert "runtime-secret" not in repr(delivery.payload)
        assert "runtime-secret" not in repr(delivery.target_source_refs)
    finally:
        await durable_fence.release()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM system_audit_events WHERE org_id = :org_id"),
                {"org_id": org_id},
            )
            await session.execute(
                text("DELETE FROM observability_operation_runs WHERE org_id = :org_id"),
                {"org_id": org_id},
            )
            await session.execute(
                text(
                    "DELETE FROM observability_scheduler_leases "
                    "WHERE lease_name = :lease_name"
                ),
                {"lease_name": lease_name},
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_enqueue_failure_rolls_back_producer_transaction() -> None:
    engine, session_factory = _isolated_database()
    org_id = f"scheduler-rollback-{uuid4()}"
    lease_name = f"test-scheduler-{uuid4()}"
    durable_fence = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=30,
        lease_name=lease_name,
    )
    assert await durable_fence.acquire() is True

    class FailingOutbox:
        async def enqueue_scheduler_run(
            self,
            session: AsyncSession,
            **_kwargs: object,
        ) -> dict[str, object]:
            session.add(
                SystemAuditEvent(
                    occurred_at=utc_now_naive(),
                    actor_subject="system:test",
                    actor_roles=["system"],
                    org_id=org_id,
                    action="must_rollback",
                    resource_type="probe",
                    resource_id=org_id,
                    success=True,
                    details={},
                )
            )
            await session.flush()
            raise RuntimeError("simulated enqueue failure")

        async def drain_ready(self, **_kwargs: object) -> object:
            raise AssertionError("run_once must not drain")

    scheduler = ObservabilityOperationsScheduler(
        session_factory,
        Settings(
            runtime_governance_enabled=True,
            observability_scheduler_run_detectors=False,
            observability_scheduler_run_policies=False,
            observability_scheduler_enable_notifications=True,
        ),
        durable_fence=durable_fence,
        notification_outbox=FailingOutbox(),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(RuntimeError, match="simulated enqueue failure"):
            await scheduler.run_once(org_id)

        async with session_factory() as session:
            run = (
                await session.execute(
                    select(ObservabilityOperationRun).where(
                        ObservabilityOperationRun.org_id == org_id
                    )
                )
            ).scalar_one()
            producer_probe_count = int(
                (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM system_audit_events "
                            "WHERE org_id = :org_id AND action = 'must_rollback'"
                        ),
                        {"org_id": org_id},
                    )
                ).scalar_one()
            )
        assert run.success is False
        assert run.error_message == "scheduler_run_failed:RuntimeError"
        assert producer_probe_count == 0
    finally:
        await durable_fence.release()
        async with session_factory() as session:
            await session.execute(
                text("DELETE FROM system_audit_events WHERE org_id = :org_id"),
                {"org_id": org_id},
            )
            await session.execute(
                text("DELETE FROM observability_operation_runs WHERE org_id = :org_id"),
                {"org_id": org_id},
            )
            await session.execute(
                text(
                    "DELETE FROM observability_scheduler_leases "
                    "WHERE lease_name = :lease_name"
                ),
                {"lease_name": lease_name},
            )
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduler_transaction_lock_blocks_expired_lease_takeover() -> None:
    """A contender cannot enter while the old leader has uncommitted writes."""
    engine, session_factory = _isolated_database()
    lease_name = f"test-scheduler-{uuid4()}"
    first = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=1,
        lease_name=lease_name,
    )
    second = PostgresSchedulerDurableFence(
        session_factory,
        lease_seconds=1,
        lease_name=lease_name,
    )
    contender: asyncio.Task[bool] | None = None

    try:
        assert await first.acquire() is True
        async with session_factory() as session:
            async with first.transaction(session):
                contender = asyncio.create_task(second.acquire())
                await asyncio.sleep(1.1)
                assert contender.done() is False

        assert contender is not None
        assert await asyncio.wait_for(contender, timeout=1) is False
    finally:
        if contender is not None and not contender.done():
            contender.cancel()
        await second.release()
        await first.release()
        async with session_factory() as session:
            await session.execute(
                text(
                    "DELETE FROM observability_scheduler_leases "
                    "WHERE lease_name = :lease_name"
                ),
                {"lease_name": lease_name},
            )
            await session.commit()
        await engine.dispose()


def _isolated_database() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        Settings().database_url,
        poolclass=NullPool,
    )
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def _insert_audit_probe(session: AsyncSession, resource_id: str) -> None:
    await session.execute(
        text(
            """
            INSERT INTO system_audit_events (
                id,
                occurred_at,
                actor_subject,
                actor_roles,
                org_id,
                action,
                resource_type,
                resource_id,
                request_id,
                success,
                details
            ) VALUES (
                gen_random_uuid(),
                timezone('utc', clock_timestamp()),
                'system:test',
                '[]'::jsonb,
                NULL,
                'scheduler_fence_probe',
                'test_probe',
                :resource_id,
                NULL,
                true,
                '{}'::jsonb
            )
            """
        ),
        {"resource_id": resource_id},
    )
