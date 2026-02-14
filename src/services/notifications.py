"""Outbound notification helpers for runtime observability events."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


def normalize_webhook_targets(targets: list[str]) -> list[str]:
    """Normalize, deduplicate, and keep valid HTTP(S) webhook URLs."""
    unique: set[str] = set()
    for target in targets:
        candidate = target.strip()
        if not candidate:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            unique.add(candidate)
    return sorted(unique)


def collect_policy_notification_targets(summary: dict[str, Any]) -> list[str]:
    """Extract webhook notification targets for breached policies."""
    targets: list[str] = []
    for result in summary.get("results", []):
        breaches = result.get("breaches") or []
        if not breaches:
            continue
        targets.extend(result.get("notification_targets") or [])
    return normalize_webhook_targets(targets)


async def send_webhook_notifications(
    targets: list[str],
    payload: dict[str, Any],
    *,
    timeout_seconds: float = 5.0,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
) -> dict[str, Any]:
    """Send a payload to each webhook target and return delivery stats."""
    normalized_targets = normalize_webhook_targets(targets)
    if not normalized_targets:
        return {"attempted": 0, "succeeded": 0, "failed": 0, "errors": []}

    attempted = 0
    succeeded = 0
    failed = 0
    errors: list[str] = []

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for target in normalized_targets:
            attempted += 1
            delivered = False
            for attempt in range(1, max(max_attempts, 1) + 1):
                try:
                    response = await client.post(target, json=payload)
                    if 200 <= response.status_code < 300:
                        succeeded += 1
                        delivered = True
                        break
                    if attempt < max_attempts:
                        await asyncio.sleep(retry_backoff_seconds * attempt)
                    else:
                        failed += 1
                        errors.append(f"{target} returned {response.status_code}")
                except Exception as exc:  # pragma: no cover - network dependent
                    if attempt < max_attempts:
                        await asyncio.sleep(retry_backoff_seconds * attempt)
                        continue
                    failed += 1
                    errors.append(f"{target} error: {exc}")
                    logger.warning(
                        "notification_delivery_failed",
                        target=target,
                        error=str(exc),
                    )
            if not delivered and max_attempts <= 0:
                failed += 1

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "errors": errors,
        "max_attempts": max(max_attempts, 1),
    }
