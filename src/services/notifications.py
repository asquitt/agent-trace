"""Outbound notification helpers for runtime observability events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)
PAGERDUTY_EVENTS_V2_URL = "https://events.pagerduty.com/v2/enqueue"
_SEVERITY_RANK = {
    "info": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}


def base_notification_result(max_attempts: int) -> dict[str, Any]:
    """Build a normalized empty result payload for notification dispatches."""
    return {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "errors": [],
        "max_attempts": max(max_attempts, 1),
        "channels": {
            "webhook": {"attempted": 0, "succeeded": 0, "failed": 0},
            "slack": {"attempted": 0, "succeeded": 0, "failed": 0},
            "pagerduty": {"attempted": 0, "succeeded": 0, "failed": 0},
        },
    }


def skipped_notification_result(
    *,
    max_attempts: int,
    reason: str,
    event_severity: str,
    min_severity: str,
) -> dict[str, Any]:
    """Build a normalized skipped-dispatch notification result payload."""
    payload = base_notification_result(max_attempts)
    payload["skipped"] = True
    payload["skip_reason"] = reason
    payload["event_severity"] = event_severity
    payload["min_severity"] = min_severity
    return payload


@dataclass(frozen=True)
class NotificationTargetSet:
    webhooks: list[str]
    slack_webhooks: list[str]
    pagerduty_routing_keys: list[str]


def normalize_webhook_targets(targets: list[str]) -> list[str]:
    """Normalize, deduplicate, and keep valid HTTP(S) webhook URLs."""
    unique: set[str] = set()
    for target in targets:
        candidate = target.strip()
        if not candidate:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            if "hooks.slack.com/" in candidate:
                continue
            unique.add(candidate)
    return sorted(unique)


def normalize_slack_webhook_targets(targets: list[str]) -> list[str]:
    """Normalize and deduplicate Slack incoming webhook URLs."""
    unique: set[str] = set()
    for target in targets:
        candidate = target.strip()
        if not candidate:
            continue
        if not (candidate.startswith("http://") or candidate.startswith("https://")):
            continue
        if "hooks.slack.com/" not in candidate:
            continue
        unique.add(candidate)
    return sorted(unique)


def normalize_pagerduty_routing_keys(targets: list[str]) -> list[str]:
    """Normalize and deduplicate PagerDuty Events API routing keys."""
    unique: set[str] = set()
    for target in targets:
        candidate = target.strip()
        if candidate:
            unique.add(candidate)
    return sorted(unique)


def classify_notification_targets(targets: list[str]) -> NotificationTargetSet:
    """Classify target strings into webhook/slack/pagerduty buckets."""
    webhooks: list[str] = []
    slack_webhooks: list[str] = []
    pagerduty_routing_keys: list[str] = []

    for target in targets:
        candidate = target.strip()
        if not candidate:
            continue

        if candidate.startswith("pagerduty:"):
            routing_key = candidate.split(":", 1)[1].strip().lstrip("/")
            if routing_key:
                pagerduty_routing_keys.append(routing_key)
            continue

        if candidate.startswith("slack:"):
            webhook = candidate.split(":", 1)[1].strip()
            if webhook:
                slack_webhooks.append(webhook)
            continue

        if candidate.startswith("http://") or candidate.startswith("https://"):
            if "hooks.slack.com/" in candidate:
                slack_webhooks.append(candidate)
            else:
                webhooks.append(candidate)

    return NotificationTargetSet(
        webhooks=normalize_webhook_targets(webhooks),
        slack_webhooks=normalize_slack_webhook_targets(slack_webhooks),
        pagerduty_routing_keys=normalize_pagerduty_routing_keys(pagerduty_routing_keys),
    )


def collect_policy_notification_target_strings(summary: dict[str, Any]) -> list[str]:
    """Extract raw notification targets for breached policies."""
    targets: list[str] = []
    for result in summary.get("results", []):
        breaches = result.get("breaches") or []
        if not breaches:
            continue
        targets.extend(result.get("notification_targets") or [])
    return sorted({target.strip() for target in targets if target and target.strip()})


def collect_policy_notification_targets(summary: dict[str, Any]) -> list[str]:
    """Extract HTTP(S) webhook targets for breached policies."""
    return normalize_webhook_targets(collect_policy_notification_target_strings(summary))


def _runtime_summary_text(payload: dict[str, Any]) -> str:
    event_type = str(payload.get("event_type", "observability_event"))
    org_id = str(payload.get("org_id", "unknown-org"))
    detector_summary = payload.get("detector_summary") or {}
    policy_summary = payload.get("policy_summary") or {}
    created_anomalies = int(detector_summary.get("created_anomalies", 0) or 0)
    deduplicated_anomalies = int(detector_summary.get("deduplicated_anomalies", 0) or 0)
    breached_policies = int(policy_summary.get("breached_policies", 0) or 0)
    return (
        f"AI Trace {event_type} org={org_id} "
        f"anomalies={created_anomalies} deduplicated={deduplicated_anomalies} "
        f"breached_policies={breached_policies}"
    )


def severity_rank(severity: str) -> int:
    """Normalize and rank runtime severities for alert-gating decisions."""
    return _SEVERITY_RANK.get(str(severity).strip().lower(), 1)


def runtime_event_severity(payload: dict[str, Any]) -> str:
    """Compute severity for a runtime event from detector and policy summaries."""
    policy_summary = payload.get("policy_summary") or {}
    detector_summary = payload.get("detector_summary") or {}

    breached_policies = int(policy_summary.get("breached_policies", 0) or 0)
    if breached_policies > 0:
        for result in policy_summary.get("results", []):
            action = ((result.get("action_result") or {}).get("action") or "").lower()
            if action == "shutdown":
                return "critical"
        return "error"

    created_anomalies = int(detector_summary.get("created_anomalies", 0) or 0)
    if created_anomalies >= 5:
        return "critical"
    if created_anomalies > 0:
        return "warning"
    return "info"


def _slack_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": _runtime_summary_text(payload),
        "attachments": [
            {
                "color": "#0f766e",
                "fields": [
                    {
                        "title": "Event",
                        "value": str(payload.get("event_type", "observability_event")),
                        "short": True,
                    },
                    {
                        "title": "Org",
                        "value": str(payload.get("org_id", "unknown-org")),
                        "short": True,
                    },
                ],
            }
        ],
    }


def _pagerduty_payload(payload: dict[str, Any], routing_key: str) -> dict[str, Any]:
    return {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": _runtime_summary_text(payload),
            "source": "ai-trace",
            "severity": runtime_event_severity(payload),
            "custom_details": payload,
        },
    }


async def _post_json_with_retry(
    client: httpx.AsyncClient,
    *,
    target: str,
    body: dict[str, Any],
    max_attempts: int,
    retry_backoff_seconds: float,
) -> tuple[bool, str | None]:
    attempt_limit = max(max_attempts, 1)
    for attempt in range(1, attempt_limit + 1):
        try:
            response = await client.post(target, json=body)
            if 200 <= response.status_code < 300:
                return True, None
            if attempt < attempt_limit:
                await asyncio.sleep(retry_backoff_seconds * attempt)
            else:
                return False, f"{target} returned {response.status_code}"
        except Exception as exc:  # pragma: no cover - network dependent
            if attempt < attempt_limit:
                await asyncio.sleep(retry_backoff_seconds * attempt)
                continue
            logger.warning("notification_delivery_failed", target=target, error=str(exc))
            return False, f"{target} error: {exc}"

    return False, f"{target} delivery failed"


async def send_runtime_notifications(
    targets: list[str],
    payload: dict[str, Any],
    *,
    slack_webhooks: list[str],
    pagerduty_routing_keys: list[str],
    timeout_seconds: float = 5.0,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> dict[str, Any]:
    """Send runtime notifications across webhook, Slack, and PagerDuty channels."""
    classified = classify_notification_targets(targets)
    webhook_targets = classified.webhooks
    slack_targets = normalize_slack_webhook_targets([*classified.slack_webhooks, *slack_webhooks])
    pagerduty_keys = normalize_pagerduty_routing_keys(
        [*classified.pagerduty_routing_keys, *pagerduty_routing_keys]
    )

    if not webhook_targets and not slack_targets and not pagerduty_keys:
        return base_notification_result(max_attempts)

    attempted = 0
    succeeded = 0
    failed = 0
    errors: list[str] = []
    channel_stats = {
        "webhook": {"attempted": 0, "succeeded": 0, "failed": 0},
        "slack": {"attempted": 0, "succeeded": 0, "failed": 0},
        "pagerduty": {"attempted": 0, "succeeded": 0, "failed": 0},
    }

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for target in webhook_targets:
            attempted += 1
            channel_stats["webhook"]["attempted"] += 1
            delivered, error = await _post_json_with_retry(
                client,
                target=target,
                body=payload,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if delivered:
                succeeded += 1
                channel_stats["webhook"]["succeeded"] += 1
            else:
                failed += 1
                channel_stats["webhook"]["failed"] += 1
                if error:
                    errors.append(error)

        slack_payload = _slack_payload(payload)
        for target in slack_targets:
            attempted += 1
            channel_stats["slack"]["attempted"] += 1
            delivered, error = await _post_json_with_retry(
                client,
                target=target,
                body=slack_payload,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if delivered:
                succeeded += 1
                channel_stats["slack"]["succeeded"] += 1
            else:
                failed += 1
                channel_stats["slack"]["failed"] += 1
                if error:
                    errors.append(error)

        for routing_key in pagerduty_keys:
            attempted += 1
            channel_stats["pagerduty"]["attempted"] += 1
            delivered, error = await _post_json_with_retry(
                client,
                target=PAGERDUTY_EVENTS_V2_URL,
                body=_pagerduty_payload(payload, routing_key),
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if delivered:
                succeeded += 1
                channel_stats["pagerduty"]["succeeded"] += 1
            else:
                failed += 1
                channel_stats["pagerduty"]["failed"] += 1
                if error:
                    errors.append(error)

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
        "max_attempts": max(max_attempts, 1),
        "channels": channel_stats,
    }


async def send_webhook_notifications(
    targets: list[str],
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 5.0,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> dict[str, Any]:
    """Backward-compatible webhook-only notification wrapper."""
    result = await send_runtime_notifications(
        targets,
        payload,
        slack_webhooks=[],
        pagerduty_routing_keys=[],
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return {
        "attempted": result["attempted"],
        "succeeded": result["succeeded"],
        "failed": result["failed"],
        "errors": result["errors"],
        "max_attempts": result["max_attempts"],
    }
