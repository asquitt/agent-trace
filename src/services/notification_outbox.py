"""Durable, secret-safe scheduler notification delivery."""

from __future__ import annotations

import hashlib
import hmac
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..models.observability import (
    BudgetPolicy,
    NotificationDelivery,
    NotificationDeliveryAttempt,
    NotificationDeliveryStatus,
    ObservabilityOperationRun,
    SystemAuditEvent,
)
from ..utils.time import utc_now_naive
from .notifications import (
    PAGERDUTY_EVENTS_V2_URL,
    NotificationDNSUnavailableError,
    _pagerduty_payload_with_dedup,
    _slack_payload,
    classify_notification_targets,
    notification_secret_values,
    notification_target_fingerprint,
    post_json_to_resolved_target,
    resolve_notification_public_target,
    runtime_event_severity,
    runtime_notification_gate_result,
    sanitize_runtime_notification_payload,
    validate_notification_https_target,
)

logger = structlog.get_logger(__name__)

_OPEN_STATUSES = {
    NotificationDeliveryStatus.PENDING.value,
    NotificationDeliveryStatus.PROCESSING.value,
    NotificationDeliveryStatus.RETRY_SCHEDULED.value,
}
_TERMINAL_STATUSES = {
    NotificationDeliveryStatus.ACCEPTED.value,
    NotificationDeliveryStatus.DEAD_LETTER.value,
    NotificationDeliveryStatus.BLOCKED.value,
    NotificationDeliveryStatus.UNCERTAIN.value,
    NotificationDeliveryStatus.CANCELLED.value,
}


@dataclass(frozen=True)
class _TargetCandidate:
    channel: str
    raw_target: str
    fingerprint: str
    source_refs: list[dict[str, str]]
    idempotency_supported: bool
    blocked_reason: str | None = None


@dataclass(frozen=True)
class ClaimedNotification:
    delivery_id: UUID
    attempt_id: UUID
    attempt_number: int
    org_id: str
    channel: str
    payload: dict[str, Any]
    target_fingerprint: str
    target_source_refs: list[dict[str, str]]
    idempotency_key: str
    idempotency_supported: bool
    max_attempts: int


@dataclass(frozen=True)
class DrainSummary:
    claimed: int = 0
    accepted: int = 0
    retry_scheduled: int = 0
    dead_letter: int = 0
    blocked: int = 0
    uncertain: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "accepted": self.accepted,
            "retry_scheduled": self.retry_scheduled,
            "dead_letter": self.dead_letter,
            "blocked": self.blocked,
            "uncertain": self.uncertain,
        }


def _stable_idempotency_key(run_id: str, channel: str, fingerprint: str) -> str:
    return hashlib.sha256(f"v1:{run_id}:{channel}:{fingerprint}".encode()).hexdigest()


def _breached_policy_ids(policy_summary: dict[str, Any]) -> list[UUID]:
    policy_ids: list[UUID] = []
    for result in policy_summary.get("results", []):
        if not isinstance(result, dict) or not result.get("breaches"):
            continue
        try:
            policy_ids.append(UUID(str(result["policy_id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return policy_ids


def _channel_targets(raw_target: str) -> list[tuple[str, str]]:
    classified = classify_notification_targets([raw_target])
    targets: list[tuple[str, str]] = []
    targets.extend(("webhook", target) for target in classified.webhooks)
    targets.extend(("slack", target) for target in classified.slack_webhooks)
    targets.extend(("pagerduty", target) for target in classified.pagerduty_routing_keys)
    return targets


class NotificationOutboxService:
    """Enqueue atomically, then claim and deliver without holding database locks."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._fingerprint_key = settings.observability_notification_fingerprint_key.get_secret_value()
        if settings.observability_scheduler_enable_notifications:
            if len(self._fingerprint_key.encode("utf-8")) < 32:
                raise ValueError(
                    "scheduled notifications require a 32-byte fingerprint key"
                )
            if settings.observability_notification_claim_seconds <= (
                settings.observability_notification_timeout_seconds
            ):
                raise ValueError(
                    "notification claim window must exceed the request timeout"
                )

    async def _target_candidates(
        self,
        session: AsyncSession,
        *,
        org_id: str,
        policy_summary: dict[str, Any],
    ) -> list[_TargetCandidate]:
        sources: list[tuple[str, dict[str, str]]] = []
        sources.extend(
            (target, {"source": "settings", "kind": "webhook"})
            for target in notification_secret_values(
                self._settings.observability_notification_webhooks
            )
        )
        sources.extend(
            (target, {"source": "settings", "kind": "slack"})
            for target in notification_secret_values(
                self._settings.observability_notification_slack_webhooks
            )
        )
        sources.extend(
            (f"pagerduty:{target}", {"source": "settings", "kind": "pagerduty"})
            for target in notification_secret_values(
                self._settings.observability_notification_pagerduty_routing_keys
            )
        )

        policy_ids = _breached_policy_ids(policy_summary)
        if policy_ids:
            rows = (
                await session.execute(
                    select(BudgetPolicy.id, BudgetPolicy.notification_targets).where(
                        BudgetPolicy.org_id == org_id,
                        BudgetPolicy.id.in_(policy_ids),
                    )
                )
            ).all()
            for policy_id, targets in rows:
                sources.extend(
                    (
                        target,
                        {"source": "policy", "policy_id": str(policy_id)},
                    )
                    for target in (targets or [])
                )

        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        idempotent_targets = {
            target.strip()
            for target in notification_secret_values(
                self._settings.observability_notification_idempotent_webhooks
            )
        }
        for source_target, source_ref in sources:
            for channel, raw_target in _channel_targets(source_target):
                fingerprint = notification_target_fingerprint(raw_target, self._fingerprint_key)
                key = (channel, fingerprint)
                current = grouped.setdefault(
                    key,
                    {
                        "raw_target": raw_target,
                        "source_refs": [],
                        "idempotency_supported": (
                            channel == "pagerduty"
                            or (channel == "webhook" and raw_target in idempotent_targets)
                        ),
                        "blocked_reason": None,
                    },
                )
                if source_ref not in current["source_refs"]:
                    current["source_refs"].append(source_ref)
                if channel in {"webhook", "slack"}:
                    try:
                        validate_notification_https_target(
                            raw_target,
                            self._settings.observability_notification_allowed_hosts,
                        )
                        if channel == "slack":
                            parsed = urlsplit(raw_target)
                            if parsed.hostname != "hooks.slack.com" or not parsed.path.startswith(
                                "/services/"
                            ):
                                raise ValueError("invalid Slack webhook target")
                    except ValueError:
                        current["blocked_reason"] = "target_policy_rejected"

        return [
            _TargetCandidate(
                channel=channel,
                fingerprint=fingerprint,
                raw_target=value["raw_target"],
                source_refs=value["source_refs"],
                idempotency_supported=value["idempotency_supported"],
                blocked_reason=value["blocked_reason"],
            )
            for (channel, fingerprint), value in sorted(grouped.items())
        ]

    async def enqueue_scheduler_run(
        self,
        session: AsyncSession,
        *,
        operation_run_id: str,
        org_id: str,
        run_started_at: str,
        detector_summary: dict[str, Any],
        policy_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Stage outbox rows in the caller's fenced producer transaction."""
        payload = sanitize_runtime_notification_payload(
            {
                "event_type": "observability_scheduler_run",
                "org_id": org_id,
                "run_started_at": run_started_at,
                "detector_summary": detector_summary,
                "policy_summary": policy_summary,
            }
        )
        severity = runtime_event_severity(payload)
        gate = runtime_notification_gate_result(
            detector_summary=payload["detector_summary"],
            policy_summary=payload["policy_summary"],
            only_on_actionable=self._settings.observability_notification_only_on_actionable,
            min_severity=self._settings.observability_notification_min_severity,
            max_attempts=self._settings.observability_notification_max_attempts,
            event_severity=severity,
        )
        if gate is not None:
            return gate

        candidates = await self._target_candidates(
            session,
            org_id=org_id,
            policy_summary=policy_summary,
        )
        if not candidates:
            return {
                "queued": 0,
                "accepted": 0,
                "blocked": 0,
                "delivery_semantics": "endpoint_acceptance",
                "skipped": True,
                "skip_reason": "no_notification_targets",
                "event_severity": severity,
            }

        now = utc_now_naive()
        run_uuid = UUID(operation_run_id)
        for candidate in candidates:
            status = (
                NotificationDeliveryStatus.BLOCKED.value
                if candidate.blocked_reason
                else NotificationDeliveryStatus.PENDING.value
            )
            session.add(
                NotificationDelivery(
                    id=uuid4(),
                    org_id=org_id,
                    operation_run_id=run_uuid,
                    event_type="observability_scheduler_run",
                    payload_version=1,
                    payload=payload,
                    channel=candidate.channel,
                    target_fingerprint=candidate.fingerprint,
                    target_source_refs=candidate.source_refs,
                    idempotency_key=_stable_idempotency_key(
                        operation_run_id,
                        candidate.channel,
                        candidate.fingerprint,
                    ),
                    idempotency_supported=candidate.idempotency_supported,
                    status=status,
                    attempt_count=0,
                    max_attempts=self._settings.observability_notification_max_attempts,
                    next_attempt_at=now,
                    terminal_at=now if candidate.blocked_reason else None,
                    last_error_code=candidate.blocked_reason,
                )
            )
        await session.flush()
        blocked = sum(candidate.blocked_reason is not None for candidate in candidates)
        return {
            "queued": len(candidates) - blocked,
            "accepted": 0,
            "blocked": blocked,
            "delivery_semantics": "endpoint_acceptance",
            "event_severity": severity,
        }

    async def _recover_expired_claims(
        self,
        session: AsyncSession,
        org_id: str,
        *,
        limit: int,
    ) -> int:
        now = utc_now_naive()
        expired = list(
            (
                await session.execute(
                    select(NotificationDelivery)
                    .where(
                        NotificationDelivery.org_id == org_id,
                        NotificationDelivery.status
                        == NotificationDeliveryStatus.PROCESSING.value,
                        NotificationDelivery.claim_expires_at <= now,
                    )
                    .order_by(
                        NotificationDelivery.claim_expires_at.asc(),
                        NotificationDelivery.id.asc(),
                    )
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
        )
        affected_run_ids: set[UUID] = set()
        for delivery in expired:
            attempt = (
                await session.execute(
                    select(NotificationDeliveryAttempt)
                    .where(
                        NotificationDeliveryAttempt.org_id == org_id,
                        NotificationDeliveryAttempt.delivery_id == delivery.id,
                        NotificationDeliveryAttempt.completed_at.is_(None),
                    )
                    .order_by(NotificationDeliveryAttempt.attempt_number.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if delivery.idempotency_supported and delivery.attempt_count < delivery.max_attempts:
                delivery.status = NotificationDeliveryStatus.RETRY_SCHEDULED.value
                delivery.next_attempt_at = now
                outcome = "abandoned_retryable"
            elif delivery.idempotency_supported:
                delivery.status = NotificationDeliveryStatus.DEAD_LETTER.value
                delivery.terminal_at = now
                outcome = NotificationDeliveryStatus.DEAD_LETTER.value
            else:
                delivery.status = NotificationDeliveryStatus.UNCERTAIN.value
                delivery.terminal_at = now
                outcome = "worker_lost_uncertain"
            delivery.claim_owner = None
            delivery.claim_expires_at = None
            delivery.last_error_code = outcome
            if attempt is not None:
                attempt.completed_at = now
                attempt.outcome = outcome
                attempt.error_code = outcome
            affected_run_ids.add(delivery.operation_run_id)
            session.add(
                SystemAuditEvent(
                    occurred_at=now,
                    actor_subject="system:notification-outbox",
                    actor_roles=["system"],
                    org_id=org_id,
                    action="notification_delivery_recovered",
                    resource_type="notification_delivery",
                    resource_id=str(delivery.id),
                    request_id=None,
                    success=False,
                    details={
                        "channel": delivery.channel,
                        "status": delivery.status,
                        "outcome": outcome,
                        "attempt_number": delivery.attempt_count,
                        "delivery_semantics": "endpoint_acceptance",
                    },
                )
            )
        await session.flush()
        for run_id in sorted(affected_run_ids, key=str):
            run = (
                await session.execute(
                    select(ObservabilityOperationRun)
                    .where(
                        ObservabilityOperationRun.id == run_id,
                        ObservabilityOperationRun.org_id == org_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is not None:
                run.notification_summary = await self._summary_for_run(
                    session,
                    run_id,
                    org_id=org_id,
                )
        return len(expired)

    async def recover_expired_claims(self, *, org_id: str, limit: int) -> int:
        """Recover a bounded set of expired claims with durable truth updates."""
        async with self._session_factory() as session:
            async with session.begin():
                return await self._recover_expired_claims(
                    session,
                    org_id,
                    limit=limit,
                )

    async def claim_ready(
        self,
        *,
        org_id: str,
        limit: int,
        worker_id: str,
        recover_expired: bool = True,
    ) -> list[ClaimedNotification]:
        """Claim due deliveries and create attempt evidence in one short transaction."""
        claimed: list[ClaimedNotification] = []
        # Commit expired-claim recovery before selecting new work. PostgreSQL may
        # otherwise omit a row whose indexed status changed earlier in the same
        # SKIP LOCKED statement transaction; a competing worker can safely win the
        # subsequent claim because that claim is independently locked.
        if recover_expired:
            await self.recover_expired_claims(org_id=org_id, limit=limit)
        now = utc_now_naive()
        async with self._session_factory() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.execute(
                            select(NotificationDelivery)
                            .where(
                                NotificationDelivery.org_id == org_id,
                                NotificationDelivery.status.in_(
                                    [
                                        NotificationDeliveryStatus.PENDING.value,
                                        NotificationDeliveryStatus.RETRY_SCHEDULED.value,
                                    ]
                                ),
                                or_(
                                    NotificationDelivery.next_attempt_at.is_(None),
                                    NotificationDelivery.next_attempt_at <= now,
                                ),
                            )
                            .order_by(
                                NotificationDelivery.next_attempt_at.asc(),
                                NotificationDelivery.created_at.asc(),
                            )
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars()
                )
                for delivery in rows:
                    delivery.status = NotificationDeliveryStatus.PROCESSING.value
                    delivery.claim_owner = worker_id
                    delivery.claim_expires_at = now + timedelta(
                        seconds=self._settings.observability_notification_claim_seconds
                    )
                    delivery.attempt_count += 1
                    attempt = NotificationDeliveryAttempt(
                        id=uuid4(),
                        org_id=delivery.org_id,
                        delivery_id=delivery.id,
                        attempt_number=delivery.attempt_count,
                        worker_id=worker_id,
                        started_at=now,
                    )
                    session.add(attempt)
                    claimed.append(
                        ClaimedNotification(
                            delivery_id=delivery.id,
                            attempt_id=attempt.id,
                            attempt_number=delivery.attempt_count,
                            org_id=delivery.org_id,
                            channel=delivery.channel,
                            payload=dict(delivery.payload),
                            target_fingerprint=delivery.target_fingerprint,
                            target_source_refs=list(delivery.target_source_refs),
                            idempotency_key=delivery.idempotency_key,
                            idempotency_supported=delivery.idempotency_supported,
                            max_attempts=delivery.max_attempts,
                        )
                    )
        return claimed

    async def _resolve_target(self, claim: ClaimedNotification) -> str | None:
        raw_targets: list[str] = []
        source_refs = claim.target_source_refs
        if any(ref.get("source") == "settings" for ref in source_refs):
            if claim.channel == "webhook":
                raw_targets.extend(
                    notification_secret_values(self._settings.observability_notification_webhooks)
                )
            elif claim.channel == "slack":
                raw_targets.extend(
                    notification_secret_values(
                        self._settings.observability_notification_slack_webhooks
                    )
                )
            elif claim.channel == "pagerduty":
                raw_targets.extend(
                    notification_secret_values(
                        self._settings.observability_notification_pagerduty_routing_keys
                    )
                )

        policy_ids = {
            ref["policy_id"]
            for ref in source_refs
            if ref.get("source") == "policy" and ref.get("policy_id")
        }
        if policy_ids:
            async with self._session_factory() as session:
                rows = (
                    await session.execute(
                        select(BudgetPolicy.notification_targets).where(
                            BudgetPolicy.org_id == claim.org_id,
                            BudgetPolicy.id.in_([UUID(value) for value in policy_ids]),
                        )
                    )
                ).scalars()
                for targets in rows:
                    raw_targets.extend(targets or [])
                await session.rollback()

        for source_target in raw_targets:
            candidate_source = (
                f"pagerduty:{source_target}"
                if claim.channel == "pagerduty" and not source_target.startswith("pagerduty:")
                else source_target
            )
            for channel, target in _channel_targets(candidate_source):
                if channel != claim.channel:
                    continue
                fingerprint = notification_target_fingerprint(target, self._fingerprint_key)
                if not hmac.compare_digest(fingerprint, claim.target_fingerprint):
                    continue
                if channel in {"webhook", "slack"}:
                    validate_notification_https_target(
                        target,
                        self._settings.observability_notification_allowed_hosts,
                    )
                    if channel == "slack":
                        parsed = urlsplit(target)
                        if parsed.hostname != "hooks.slack.com" or not parsed.path.startswith(
                            "/services/"
                        ):
                            raise ValueError("invalid Slack webhook target")
                return target
        return None

    def _retry_status(self, claim: ClaimedNotification) -> str:
        if claim.idempotency_supported and claim.attempt_number < claim.max_attempts:
            return NotificationDeliveryStatus.RETRY_SCHEDULED.value
        if claim.idempotency_supported:
            return NotificationDeliveryStatus.DEAD_LETTER.value
        return NotificationDeliveryStatus.UNCERTAIN.value

    async def _finalize(
        self,
        claim: ClaimedNotification,
        *,
        worker_id: str,
        status: str,
        outcome: str,
        http_status: int | None = None,
        error_code: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        now = utc_now_naive()
        async with self._session_factory() as session:
            async with session.begin():
                delivery = (
                    await session.execute(
                        select(NotificationDelivery)
                        .where(
                            NotificationDelivery.id == claim.delivery_id,
                            NotificationDelivery.org_id == claim.org_id,
                            NotificationDelivery.status
                            == NotificationDeliveryStatus.PROCESSING.value,
                            NotificationDelivery.claim_owner == worker_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if delivery is None:
                    raise RuntimeError("notification claim is no longer current")
                attempt = (
                    await session.execute(
                        select(NotificationDeliveryAttempt)
                        .where(
                            NotificationDeliveryAttempt.id == claim.attempt_id,
                            NotificationDeliveryAttempt.org_id == claim.org_id,
                            NotificationDeliveryAttempt.delivery_id == claim.delivery_id,
                            NotificationDeliveryAttempt.worker_id == worker_id,
                            NotificationDeliveryAttempt.completed_at.is_(None),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if attempt is None:
                    raise RuntimeError("notification attempt is no longer current")

                delivery.status = status
                delivery.claim_owner = None
                delivery.claim_expires_at = None
                delivery.last_http_status = http_status
                delivery.last_error_code = error_code
                delivery.provider_request_id = provider_request_id
                if status == NotificationDeliveryStatus.ACCEPTED.value:
                    delivery.accepted_at = now
                    delivery.terminal_at = now
                elif status in _TERMINAL_STATUSES:
                    delivery.terminal_at = now
                elif status == NotificationDeliveryStatus.RETRY_SCHEDULED.value:
                    backoff = self._settings.observability_notification_retry_backoff_seconds * (
                        2 ** max(claim.attempt_number - 1, 0)
                    )
                    delivery.next_attempt_at = now + timedelta(seconds=backoff)

                attempt.completed_at = now
                attempt.outcome = status
                attempt.http_status = http_status
                attempt.error_code = error_code
                attempt.provider_request_id = provider_request_id
                session.add(
                    SystemAuditEvent(
                        occurred_at=now,
                        actor_subject="system:notification-outbox",
                        actor_roles=["system"],
                        org_id=claim.org_id,
                        action="notification_delivery_finalized",
                        resource_type="notification_delivery",
                        resource_id=str(delivery.id),
                        request_id=None,
                        success=status == NotificationDeliveryStatus.ACCEPTED.value,
                        details={
                            "channel": delivery.channel,
                            "status": status,
                            "outcome": outcome,
                            "attempt_number": claim.attempt_number,
                            "http_status": http_status,
                            "error_code": error_code,
                            "delivery_semantics": "endpoint_acceptance",
                        },
                    )
                )
                await session.flush()
                run = (
                    await session.execute(
                        select(ObservabilityOperationRun)
                        .where(
                            ObservabilityOperationRun.id == delivery.operation_run_id,
                            ObservabilityOperationRun.org_id == claim.org_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one()
                run.notification_summary = await self._summary_for_run(
                    session,
                    delivery.operation_run_id,
                    org_id=claim.org_id,
                )

    async def _summary_for_run(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        org_id: str,
    ) -> dict[str, Any]:
        rows = (
            await session.execute(
                select(NotificationDelivery.status, func.count(NotificationDelivery.id))
                .where(
                    NotificationDelivery.operation_run_id == run_id,
                    NotificationDelivery.org_id == org_id,
                )
                .group_by(NotificationDelivery.status)
            )
        ).all()
        counts = Counter({str(status): int(count) for status, count in rows})
        return {
            "queued": sum(counts[status] for status in _OPEN_STATUSES),
            "accepted": counts[NotificationDeliveryStatus.ACCEPTED.value],
            "retry_scheduled": counts[NotificationDeliveryStatus.RETRY_SCHEDULED.value],
            "dead_letter": counts[NotificationDeliveryStatus.DEAD_LETTER.value],
            "blocked": counts[NotificationDeliveryStatus.BLOCKED.value],
            "uncertain": counts[NotificationDeliveryStatus.UNCERTAIN.value],
            "cancelled": counts[NotificationDeliveryStatus.CANCELLED.value],
            "total": sum(counts.values()),
            "delivery_semantics": "endpoint_acceptance",
        }

    async def _deliver_one(
        self,
        claim: ClaimedNotification,
        *,
        worker_id: str,
    ) -> str:
        try:
            target = await self._resolve_target(claim)
        except ValueError:
            await self._finalize(
                claim,
                worker_id=worker_id,
                status=NotificationDeliveryStatus.BLOCKED.value,
                outcome="target_policy_rejected",
                error_code="target_policy_rejected",
            )
            return NotificationDeliveryStatus.BLOCKED.value
        if target is None:
            await self._finalize(
                claim,
                worker_id=worker_id,
                status=NotificationDeliveryStatus.BLOCKED.value,
                outcome="target_rotated_or_missing",
                error_code="target_rotated_or_missing",
            )
            return NotificationDeliveryStatus.BLOCKED.value

        headers: dict[str, str] = {}
        body = claim.payload
        post_target = target
        if claim.channel == "slack":
            body = _slack_payload(claim.payload)
        elif claim.channel == "pagerduty":
            post_target = PAGERDUTY_EVENTS_V2_URL
            body = _pagerduty_payload_with_dedup(
                claim.payload,
                target,
                dedup_key=claim.idempotency_key,
            )
        elif claim.idempotency_supported:
            headers["Idempotency-Key"] = claim.idempotency_key

        try:
            resolved = await resolve_notification_public_target(post_target)
        except NotificationDNSUnavailableError:
            status = (
                NotificationDeliveryStatus.RETRY_SCHEDULED.value
                if claim.attempt_number < claim.max_attempts
                else NotificationDeliveryStatus.DEAD_LETTER.value
            )
            await self._finalize(
                claim,
                worker_id=worker_id,
                status=status,
                outcome="dns_unavailable",
                error_code="dns_unavailable",
            )
            return status
        except ValueError:
            await self._finalize(
                claim,
                worker_id=worker_id,
                status=NotificationDeliveryStatus.BLOCKED.value,
                outcome="target_policy_rejected",
                error_code="target_policy_rejected",
            )
            return NotificationDeliveryStatus.BLOCKED.value

        try:
            response = await post_json_to_resolved_target(
                resolved=resolved,
                body=body,
                headers=headers,
                timeout_seconds=self._settings.observability_notification_timeout_seconds,
            )
        except Exception as exc:  # network outcome is ambiguous
            status = self._retry_status(claim)
            error_code = f"transport_{type(exc).__name__}"
            await self._finalize(
                claim,
                worker_id=worker_id,
                status=status,
                outcome="transport_ambiguous",
                error_code=error_code,
            )
            return status

        provider_request_id = response.headers.get("x-request-id")
        if provider_request_id:
            provider_request_id = hashlib.sha256(
                provider_request_id.encode("utf-8", errors="replace")
            ).hexdigest()
        if 200 <= response.status_code < 300:
            status = NotificationDeliveryStatus.ACCEPTED.value
            outcome = "endpoint_accepted"
            error_code = None
        elif 300 <= response.status_code < 400:
            status = NotificationDeliveryStatus.DEAD_LETTER.value
            outcome = "redirect_rejected"
            error_code = f"http_{response.status_code}"
        elif 400 <= response.status_code < 500 and response.status_code not in {
            408,
            425,
            429,
        }:
            status = NotificationDeliveryStatus.DEAD_LETTER.value
            outcome = "permanent_rejection"
            error_code = f"http_{response.status_code}"
        else:
            status = self._retry_status(claim)
            outcome = "ambiguous_or_retryable_response"
            error_code = f"http_{response.status_code}"
        await self._finalize(
            claim,
            worker_id=worker_id,
            status=status,
            outcome=outcome,
            http_status=response.status_code,
            error_code=error_code,
            provider_request_id=provider_request_id,
        )
        return status

    async def drain_ready(self, *, org_id: str, limit: int) -> DrainSummary:
        """Claim each row immediately before delivery; never lease a serial batch."""
        worker_id = str(uuid4())
        counts: Counter[str] = Counter()
        claimed_count = 0
        await self.recover_expired_claims(org_id=org_id, limit=limit)
        for _ in range(limit):
            claims = await self.claim_ready(
                org_id=org_id,
                limit=1,
                worker_id=worker_id,
                recover_expired=False,
            )
            if not claims:
                break
            claim = claims[0]
            claimed_count += 1
            status = await self._deliver_one(claim, worker_id=worker_id)
            counts[status] += 1
        return DrainSummary(
            claimed=claimed_count,
            accepted=counts[NotificationDeliveryStatus.ACCEPTED.value],
            retry_scheduled=counts[NotificationDeliveryStatus.RETRY_SCHEDULED.value],
            dead_letter=counts[NotificationDeliveryStatus.DEAD_LETTER.value],
            blocked=counts[NotificationDeliveryStatus.BLOCKED.value],
            uncertain=counts[NotificationDeliveryStatus.UNCERTAIN.value],
        )

    async def durable_failure_count(self, *, org_id: str) -> int:
        """Count durable terminal outcomes that require operator attention."""
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count(NotificationDelivery.id)).where(
                    NotificationDelivery.org_id == org_id,
                    NotificationDelivery.status.in_(
                        [
                            NotificationDeliveryStatus.DEAD_LETTER.value,
                            NotificationDeliveryStatus.BLOCKED.value,
                            NotificationDeliveryStatus.UNCERTAIN.value,
                        ]
                    ),
                )
            )
            await session.rollback()
        return int(count or 0)

    async def cleanup_terminal_deliveries(self, *, org_id: str, limit: int) -> int:
        """Delete only conclusively terminal rows beyond the retention boundary."""
        cutoff = utc_now_naive() - timedelta(
            days=self._settings.observability_notification_retention_days
        )
        async with self._session_factory() as session:
            async with session.begin():
                rows = list(
                    (
                        await session.execute(
                            select(NotificationDelivery)
                            .where(
                                NotificationDelivery.org_id == org_id,
                                NotificationDelivery.status.in_(
                                    [
                                        NotificationDeliveryStatus.ACCEPTED.value,
                                        NotificationDeliveryStatus.DEAD_LETTER.value,
                                        NotificationDeliveryStatus.BLOCKED.value,
                                        NotificationDeliveryStatus.CANCELLED.value,
                                    ]
                                ),
                                NotificationDelivery.terminal_at < cutoff,
                            )
                            .order_by(
                                NotificationDelivery.terminal_at.asc(),
                                NotificationDelivery.id.asc(),
                            )
                            .limit(limit)
                            .with_for_update(skip_locked=True)
                        )
                    ).scalars()
                )
                for row in rows:
                    await session.delete(row)
        return len(rows)
