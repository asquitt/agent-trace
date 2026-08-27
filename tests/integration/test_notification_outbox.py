"""PostgreSQL contract tests for durable notification delivery."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import Settings
from src.models import (
    NotificationDelivery,
    NotificationDeliveryAttempt,
    ObservabilityOperationRun,
    SystemAuditEvent,
)
from src.services.notification_outbox import NotificationOutboxService
from src.utils.time import utc_now_naive


def _database() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(Settings().database_url, poolclass=NullPool)
    return engine, async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


def _settings(target: str, *, idempotent: bool) -> Settings:
    return Settings(
        observability_notification_only_on_actionable=False,
        observability_notification_min_severity="info",
        observability_notification_webhooks=[target],
        observability_notification_idempotent_webhooks=[target] if idempotent else [],
        observability_notification_allowed_hosts=["hooks.example.com"],
        observability_notification_fingerprint_key="f" * 32,
        observability_notification_max_attempts=3,
        observability_notification_retry_backoff_seconds=0,
    )


async def _seed_run(
    factory: async_sessionmaker[AsyncSession],
    *,
    org_id: str,
) -> ObservabilityOperationRun:
    row = ObservabilityOperationRun(
        id=uuid4(),
        org_id=org_id,
        run_type="scheduler",
        started_at=utc_now_naive(),
        completed_at=utc_now_naive(),
        success=True,
        detector_summary={},
        policy_summary={},
        notification_summary={},
        run_metadata={},
    )
    async with factory() as session:
        session.add(row)
        await session.commit()
    return row


async def _enqueue(
    service: NotificationOutboxService,
    factory: async_sessionmaker[AsyncSession],
    run: ObservabilityOperationRun,
    *,
    nested_secret: str,
) -> dict[str, Any]:
    async with factory() as session:
        async with session.begin():
            return await service.enqueue_scheduler_run(
                session,
                operation_run_id=str(run.id),
                org_id=run.org_id,
                run_started_at=run.started_at.isoformat(),
                detector_summary={
                    "created_anomalies": 1,
                    "deduplicated_anomalies": 0,
                    "metadata": {"authorization": nested_secret},
                },
                policy_summary={
                    "evaluated_policies": 1,
                    "breached_policies": 1,
                    "results": [
                        {
                            "policy_id": str(uuid4()),
                            "breaches": [
                                {
                                    "trigger_type": "max_cost_usd",
                                    "observed_value": 3,
                                    "threshold_value": 2,
                                }
                            ],
                            "notification_targets": [nested_secret],
                            "details": {"cookie": nested_secret},
                        }
                    ],
                },
            )


async def _cleanup(factory: async_sessionmaker[AsyncSession], org_id: str) -> None:
    async with factory() as session:
        await session.execute(delete(SystemAuditEvent).where(SystemAuditEvent.org_id == org_id))
        await session.execute(
            delete(ObservabilityOperationRun).where(ObservabilityOperationRun.org_id == org_id)
        )
        await session.commit()


@pytest.mark.asyncio
async def test_outbox_enqueue_and_claim_are_atomic_and_secret_free() -> None:
    engine, factory = _database()
    org_id = f"outbox-claim-{uuid4()}"
    target = "https://hooks.example.com/credential-value"
    nested_secret = "Bearer should-never-persist"
    service = NotificationOutboxService(factory, _settings(target, idempotent=True))
    run = await _seed_run(factory, org_id=org_id)
    try:
        summary = await _enqueue(
            service,
            factory,
            run,
            nested_secret=nested_secret,
        )
        assert summary["queued"] == 1
        first, second = await asyncio.gather(
            service.claim_ready(org_id=org_id, limit=1, worker_id="worker-a"),
            service.claim_ready(org_id=org_id, limit=1, worker_id="worker-b"),
        )
        claims = [*first, *second]
        assert len(claims) == 1
        assert claims[0].attempt_number == 1

        async with factory() as session:
            delivery = (
                await session.execute(
                    select(NotificationDelivery).where(NotificationDelivery.org_id == org_id)
                )
            ).scalar_one()
            attempt = (
                await session.execute(
                    select(NotificationDeliveryAttempt).where(
                        NotificationDeliveryAttempt.delivery_id == delivery.id
                    )
                )
            ).scalar_one()
        serialized = repr(
            {
                "payload": delivery.payload,
                "refs": delivery.target_source_refs,
                "error": delivery.last_error_code,
            }
        )
        assert target not in serialized
        assert nested_secret not in serialized
        assert attempt.outcome == "in_progress"
    finally:
        await _cleanup(factory, org_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_idempotent_acceptance_updates_only_delivery_truth() -> None:
    engine, factory = _database()
    org_id = f"outbox-accepted-{uuid4()}"
    target = "https://hooks.example.com/receiver"
    service = NotificationOutboxService(factory, _settings(target, idempotent=True))
    run = await _seed_run(factory, org_id=org_id)
    try:
        await _enqueue(service, factory, run, nested_secret="private-routing-key")
        claim = (await service.claim_ready(org_id=org_id, limit=1, worker_id="worker"))[0]
        calls: list[dict[str, Any]] = []

        class FakeClient:
            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                calls.append({"url": url, **kwargs})
                return httpx.Response(202, headers={"x-request-id": "provider-1"})

        async def resolve(_claim: Any) -> str:
            return target

        service._resolve_target = resolve  # type: ignore[method-assign]  # noqa: SLF001
        status = await service._deliver_one(  # noqa: SLF001
            FakeClient(),  # type: ignore[arg-type]
            claim,
            worker_id="worker",
        )
        assert status == "accepted"
        assert calls[0]["headers"]["Idempotency-Key"] == claim.idempotency_key

        async with factory() as session:
            delivery = (
                await session.execute(
                    select(NotificationDelivery).where(NotificationDelivery.org_id == org_id)
                )
            ).scalar_one()
            persisted_run = await session.get(ObservabilityOperationRun, run.id)
        assert delivery.status == "accepted"
        assert delivery.accepted_at is not None
        assert persisted_run is not None
        assert persisted_run.success is True
        assert persisted_run.notification_summary["accepted"] == 1
        assert persisted_run.notification_summary["delivery_semantics"] == (
            "endpoint_acceptance"
        )
    finally:
        await _cleanup(factory, org_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_non_idempotent_ambiguous_timeout_is_never_retried() -> None:
    engine, factory = _database()
    org_id = f"outbox-uncertain-{uuid4()}"
    target = "https://hooks.example.com/non-idempotent"
    service = NotificationOutboxService(factory, _settings(target, idempotent=False))
    run = await _seed_run(factory, org_id=org_id)
    try:
        await _enqueue(service, factory, run, nested_secret="secret-exception-text")
        claim = (await service.claim_ready(org_id=org_id, limit=1, worker_id="worker"))[0]

        class TimeoutClient:
            async def post(self, _url: str, **_kwargs: Any) -> httpx.Response:
                raise httpx.ReadTimeout("secret-exception-text")

        async def resolve(_claim: Any) -> str:
            return target

        service._resolve_target = resolve  # type: ignore[method-assign]  # noqa: SLF001
        status = await service._deliver_one(  # noqa: SLF001
            TimeoutClient(),  # type: ignore[arg-type]
            claim,
            worker_id="worker",
        )
        assert status == "uncertain"
        assert await service.claim_ready(org_id=org_id, limit=1, worker_id="other") == []

        async with factory() as session:
            delivery = (
                await session.execute(
                    select(NotificationDelivery).where(NotificationDelivery.org_id == org_id)
                )
            ).scalar_one()
        assert delivery.status == "uncertain"
        assert delivery.last_error_code == "transport_ReadTimeout"
        assert "secret-exception-text" not in repr(delivery.last_error_code)
    finally:
        await _cleanup(factory, org_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_idempotent_claim_reuses_key_through_bounded_retry() -> None:
    engine, factory = _database()
    org_id = f"outbox-retry-{uuid4()}"
    target = "https://hooks.example.com/idempotent"
    service = NotificationOutboxService(factory, _settings(target, idempotent=True))
    run = await _seed_run(factory, org_id=org_id)
    try:
        await _enqueue(service, factory, run, nested_secret="never-persist-this")
        first = (await service.claim_ready(org_id=org_id, limit=1, worker_id="crashed"))[0]
        async with factory() as session:
            delivery = await session.get(NotificationDelivery, first.delivery_id)
            assert delivery is not None
            delivery.claim_expires_at = utc_now_naive() - timedelta(seconds=1)
            await session.commit()

        second = (await service.claim_ready(org_id=org_id, limit=1, worker_id="worker-2"))[0]
        assert second.attempt_number == 2
        assert second.idempotency_key == first.idempotency_key
        calls: list[dict[str, Any]] = []

        class ResponseClient:
            def __init__(self) -> None:
                self.statuses = [503, 202]

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                calls.append({"url": url, **kwargs})
                return httpx.Response(self.statuses.pop(0))

        async def resolve(_claim: Any) -> str:
            return target

        service._resolve_target = resolve  # type: ignore[method-assign]  # noqa: SLF001
        client = ResponseClient()
        assert (
            await service._deliver_one(  # noqa: SLF001
                client,  # type: ignore[arg-type]
                second,
                worker_id="worker-2",
            )
            == "retry_scheduled"
        )
        third = (await service.claim_ready(org_id=org_id, limit=1, worker_id="worker-3"))[0]
        assert third.idempotency_key == first.idempotency_key
        assert (
            await service._deliver_one(  # noqa: SLF001
                client,  # type: ignore[arg-type]
                third,
                worker_id="worker-3",
            )
            == "accepted"
        )
        assert [call["headers"]["Idempotency-Key"] for call in calls] == [
            first.idempotency_key,
            first.idempotency_key,
        ]

        async with factory() as session:
            attempts = list(
                (
                    await session.execute(
                        select(NotificationDeliveryAttempt)
                        .where(NotificationDeliveryAttempt.delivery_id == first.delivery_id)
                        .order_by(NotificationDeliveryAttempt.attempt_number)
                    )
                ).scalars()
            )
        assert [attempt.outcome for attempt in attempts] == [
            "abandoned_retryable",
            "retry_scheduled",
            "accepted",
        ]
    finally:
        await _cleanup(factory, org_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pagerduty_retries_reuse_provider_dedup_key() -> None:
    engine, factory = _database()
    org_id = f"outbox-pagerduty-{uuid4()}"
    routing_key = "pagerduty-routing-secret"
    settings = Settings(
        observability_notification_only_on_actionable=False,
        observability_notification_min_severity="info",
        observability_notification_pagerduty_routing_keys=[routing_key],
        observability_notification_fingerprint_key="f" * 32,
        observability_notification_max_attempts=2,
        observability_notification_retry_backoff_seconds=0,
    )
    service = NotificationOutboxService(factory, settings)
    run = await _seed_run(factory, org_id=org_id)
    try:
        await _enqueue(service, factory, run, nested_secret="nested-private-value")
        first = (await service.claim_ready(org_id=org_id, limit=1, worker_id="pd-1"))[0]
        calls: list[dict[str, Any]] = []

        class PagerDutyClient:
            def __init__(self) -> None:
                self.statuses = [503, 202]

            async def post(self, url: str, **kwargs: Any) -> httpx.Response:
                calls.append({"url": url, **kwargs})
                return httpx.Response(self.statuses.pop(0))

        async def resolve(_claim: Any) -> str:
            return routing_key

        service._resolve_target = resolve  # type: ignore[method-assign]  # noqa: SLF001
        client = PagerDutyClient()
        assert (
            await service._deliver_one(  # noqa: SLF001
                client,  # type: ignore[arg-type]
                first,
                worker_id="pd-1",
            )
            == "retry_scheduled"
        )
        second = (await service.claim_ready(org_id=org_id, limit=1, worker_id="pd-2"))[0]
        assert (
            await service._deliver_one(  # noqa: SLF001
                client,  # type: ignore[arg-type]
                second,
                worker_id="pd-2",
            )
            == "accepted"
        )
        assert [call["json"]["dedup_key"] for call in calls] == [
            first.idempotency_key,
            first.idempotency_key,
        ]
        assert all(call["url"] == "https://events.pagerduty.com/v2/enqueue" for call in calls)

        async with factory() as session:
            delivery = await session.get(NotificationDelivery, first.delivery_id)
        assert delivery is not None
        assert routing_key not in repr(delivery.payload)
        assert routing_key not in repr(delivery.target_source_refs)
    finally:
        await _cleanup(factory, org_id)
        await engine.dispose()
