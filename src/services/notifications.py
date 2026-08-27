"""Outbound notification helpers for runtime observability events."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import httpcore
import httpx
import structlog
from pydantic import SecretStr

logger = structlog.get_logger(__name__)
PAGERDUTY_EVENTS_V2_URL = "https://events.pagerduty.com/v2/enqueue"
_SEVERITY_RANK = {
    "info": 1,
    "warning": 2,
    "error": 3,
    "critical": 4,
}

_SENSITIVE_NOTIFICATION_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "notification_targets",
    "password",
    "routing_key",
    "secret",
    "set_cookie",
    "target_webhook",
    "token",
    "webhook",
    "webhook_url",
    "x_api_key",
}
_SENSITIVE_NOTIFICATION_SUFFIXES = (
    "_api_key",
    "_authorization",
    "_cookie",
    "_password",
    "_routing_key",
    "_secret",
    "_token",
    "_webhook",
    "_webhook_url",
)


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


@dataclass(frozen=True)
class ResolvedNotificationTarget:
    """A validated target paired with the exact public addresses it resolved to."""

    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


class NotificationDNSUnavailableError(OSError):
    """Raised when a target cannot be resolved before any request is attempted."""


def notification_secret_values(values: Sequence[str | SecretStr]) -> list[str]:
    """Reveal notification secrets only at the outbound boundary."""
    return [
        value.get_secret_value() if isinstance(value, SecretStr) else value
        for value in values
    ]


def normalize_webhook_targets(targets: Sequence[str | SecretStr]) -> list[str]:
    """Normalize, deduplicate, and keep valid HTTP(S) webhook URLs."""
    unique: set[str] = set()
    for target in notification_secret_values(targets):
        candidate = target.strip()
        if not candidate:
            continue
        if candidate.startswith("http://") or candidate.startswith("https://"):
            if "hooks.slack.com/" in candidate:
                continue
            unique.add(candidate)
    return sorted(unique)


def normalize_slack_webhook_targets(targets: Sequence[str | SecretStr]) -> list[str]:
    """Normalize and deduplicate Slack incoming webhook URLs."""
    unique: set[str] = set()
    for target in notification_secret_values(targets):
        candidate = target.strip()
        if not candidate:
            continue
        if not (candidate.startswith("http://") or candidate.startswith("https://")):
            continue
        if "hooks.slack.com/" not in candidate:
            continue
        unique.add(candidate)
    return sorted(unique)


def normalize_pagerduty_routing_keys(targets: Sequence[str | SecretStr]) -> list[str]:
    """Normalize and deduplicate PagerDuty Events API routing keys."""
    unique: set[str] = set()
    for target in notification_secret_values(targets):
        candidate = target.strip()
        if candidate:
            unique.add(candidate)
    return sorted(unique)


def classify_notification_targets(targets: Sequence[str | SecretStr]) -> NotificationTargetSet:
    """Classify target strings into webhook/slack/pagerduty buckets."""
    webhooks: list[str] = []
    slack_webhooks: list[str] = []
    pagerduty_routing_keys: list[str] = []

    for target in notification_secret_values(targets):
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


def notification_target_fingerprint(target: str, fingerprint_key: str) -> str:
    """Return a keyed, irreversible destination fingerprint safe for persistence."""
    if not fingerprint_key:
        raise ValueError("notification fingerprint key is required")
    return hmac.new(
        fingerprint_key.encode("utf-8"),
        target.strip().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_notification_https_target(target: str, allowed_hosts: list[str]) -> str:
    """Validate an outbound webhook against an exact HTTPS host allowlist."""
    parsed = urlsplit(target.strip())
    hostname = (parsed.hostname or "").lower().rstrip(".")
    allowed = {host.strip().lower().rstrip(".") for host in allowed_hosts if host.strip()}
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("notification target has an invalid port") from exc
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or port not in (None, 443)
    ):
        raise ValueError("notification target must be an HTTPS URL without userinfo")
    if hostname not in allowed:
        raise ValueError("notification target host is not allowlisted")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            hostname.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ValueError("notification target hostname is invalid") from exc
        return target.strip()
    raise ValueError("notification target must use a DNS hostname")


async def resolve_notification_public_target(target: str) -> ResolvedNotificationTarget:
    """Resolve a target once and retain only an all-public address set."""
    parsed = urlsplit(target)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("notification target has no hostname")
    try:
        hostname = hostname.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("notification target hostname is invalid") from exc
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("notification target has an invalid port") from exc
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise NotificationDNSUnavailableError("notification target DNS is unavailable") from exc
    resolved = {
        ipaddress.ip_address(item[4][0]).compressed
        for item in addresses
    }
    if not resolved:
        raise NotificationDNSUnavailableError("notification target DNS is unavailable")
    if any(not ipaddress.ip_address(address).is_global for address in resolved):
        raise ValueError("notification target did not resolve exclusively to public addresses")
    return ResolvedNotificationTarget(
        url=target,
        hostname=hostname,
        port=port,
        addresses=tuple(
            sorted(
                resolved,
                key=lambda address: (
                    ipaddress.ip_address(address).version,
                    ipaddress.ip_address(address).packed,
                ),
            )
        ),
    )


async def validate_notification_public_dns(target: str) -> None:
    """Reject targets that resolve to any non-public address."""
    await resolve_notification_public_target(target)


class _PinnedAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect an HTTP origin only through its frozen validated address set."""

    def __init__(self, resolved: ResolvedNotificationTarget) -> None:
        self._hostname = resolved.hostname.lower().rstrip(".")
        self._port = resolved.port
        self._addresses = tuple(
            ipaddress.ip_address(address).compressed for address in resolved.addresses
        )
        if not self._addresses or any(
            not ipaddress.ip_address(address).is_global for address in self._addresses
        ):
            raise ValueError("notification transport requires public addresses")
        self._delegate = cast(httpcore.AsyncNetworkBackend, httpcore.AnyIOBackend())

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        if host.lower().rstrip(".") != self._hostname or port != self._port:
            raise httpcore.ConnectError("notification transport origin mismatch")
        deadline = time.monotonic() + timeout if timeout is not None else None
        last_error: Exception | None = None
        for address in self._addresses:
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            if remaining == 0.0:
                raise httpcore.ConnectTimeout("notification connect timeout")
            try:
                return await self._delegate.connect_tcp(
                    host=address,
                    port=port,
                    timeout=remaining,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("notification transport has no addresses")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> httpcore.AsyncNetworkStream:
        _ = (path, timeout, socket_options)
        raise httpcore.ConnectError("notification transport forbids unix sockets")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class _PinnedResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._stream:
            yield part

    async def aclose(self) -> None:
        await self._stream.aclose()


class _PinnedAsyncTransport(httpx.AsyncBaseTransport):
    """HTTPX transport backed by a frozen-address HTTPcore pool."""

    def __init__(self, resolved: ResolvedNotificationTarget) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=_PinnedAsyncNetworkBackend(resolved),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_PinnedResponseStream(response.stream),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


async def post_json_to_resolved_target(
    *,
    resolved: ResolvedNotificationTarget,
    body: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout_seconds: float,
) -> httpx.Response:
    """POST to a validated IP while retaining hostname TLS and HTTP identity."""
    async with httpx.AsyncClient(
        transport=_PinnedAsyncTransport(resolved),
        timeout=timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return await client.post(resolved.url, json=body, headers=headers or {})


def sanitize_runtime_notification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively copy a payload while removing credential-bearing fields."""

    def sensitive_key(key: Any) -> bool:
        normalized = str(key).strip().lower().replace("-", "_")
        return normalized in _SENSITIVE_NOTIFICATION_KEYS or normalized.endswith(
            _SENSITIVE_NOTIFICATION_SUFFIXES
        )

    def sanitize(value: Any) -> Any:
        if isinstance(value, SecretStr):
            return "[redacted]"
        if isinstance(value, dict):
            return {
                str(key): sanitize(item)
                for key, item in value.items()
                if not sensitive_key(key)
            }
        if isinstance(value, (list, tuple)):
            return [sanitize(item) for item in value]
        return value

    sanitized = sanitize(payload)
    if not isinstance(sanitized, dict):  # defensive; the public contract is a mapping
        raise TypeError("notification payload must be an object")
    return sanitized


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


def merge_runtime_notification_targets(
    *,
    base_targets: Sequence[str | SecretStr],
    policy_summary: dict[str, Any] | None = None,
    extra_targets: list[str] | None = None,
) -> list[str]:
    """Merge, normalize, and deduplicate raw runtime notification target strings."""
    targets = notification_secret_values(base_targets)
    targets.extend(collect_policy_notification_target_strings(policy_summary or {}))
    targets.extend(extra_targets or [])
    return sorted({target.strip() for target in targets if target and target.strip()})


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


def runtime_notification_gate_result(
    *,
    detector_summary: dict[str, Any] | None,
    policy_summary: dict[str, Any] | None,
    only_on_actionable: bool,
    min_severity: str,
    max_attempts: int,
    event_severity: str,
) -> dict[str, Any] | None:
    """Return skip payload when runtime notification should be suppressed."""
    created_anomalies = int((detector_summary or {}).get("created_anomalies", 0) or 0)
    breached_policies = int((policy_summary or {}).get("breached_policies", 0) or 0)

    if only_on_actionable and created_anomalies == 0 and breached_policies == 0:
        return skipped_notification_result(
            max_attempts=max_attempts,
            reason="no_actionable_findings",
            event_severity=event_severity,
            min_severity=min_severity,
        )

    if severity_rank(event_severity) < severity_rank(min_severity):
        return skipped_notification_result(
            max_attempts=max_attempts,
            reason="below_min_severity",
            event_severity=event_severity,
            min_severity=min_severity,
        )

    return None


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


def _pagerduty_payload_with_dedup(
    payload: dict[str, Any],
    routing_key: str,
    *,
    dedup_key: str | None,
) -> dict[str, Any]:
    body = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": _runtime_summary_text(payload),
            "source": "ai-trace",
            "severity": runtime_event_severity(payload),
            "custom_details": payload,
        },
    }
    if dedup_key:
        body["dedup_key"] = dedup_key
    return body


async def _post_json_with_retry(
    *,
    target: str,
    body: dict[str, Any],
    max_attempts: int,
    retry_backoff_seconds: float,
    idempotency_supported: bool,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[bool, str | None]:
    attempt_limit = max(max_attempts, 1)
    for attempt in range(1, attempt_limit + 1):
        try:
            resolved = await resolve_notification_public_target(target)
        except NotificationDNSUnavailableError:
            if attempt < attempt_limit:
                await asyncio.sleep(retry_backoff_seconds * attempt)
                continue
            return False, "dns_unavailable"
        except ValueError:
            return False, "target_policy_rejected"
        try:
            response = await post_json_to_resolved_target(
                resolved=resolved,
                body=body,
                headers=headers,
                timeout_seconds=timeout_seconds,
            )
            if 200 <= response.status_code < 300:
                return True, None
            if idempotency_supported and attempt < attempt_limit:
                await asyncio.sleep(retry_backoff_seconds * attempt)
            else:
                return False, f"http_status:{response.status_code}"
        except Exception as exc:  # pragma: no cover - network dependent
            if idempotency_supported and attempt < attempt_limit:
                await asyncio.sleep(retry_backoff_seconds * attempt)
                continue
            error_code = type(exc).__name__
            logger.warning("notification_delivery_failed", error_code=error_code)
            return False, f"delivery_error:{error_code}"

    return False, "delivery_failed"


async def send_runtime_notifications(
    targets: Sequence[str | SecretStr],
    payload: dict[str, Any],
    *,
    slack_webhooks: Sequence[str | SecretStr],
    pagerduty_routing_keys: Sequence[str | SecretStr],
    timeout_seconds: float = 5.0,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.5,
    allowed_hosts: list[str] | None = None,
    idempotent_webhooks: Sequence[str | SecretStr] = (),
    fingerprint_key: str | SecretStr = "",
) -> dict[str, Any]:
    """Send runtime notifications across webhook, Slack, and PagerDuty channels."""
    payload = sanitize_runtime_notification_payload(payload)
    fingerprint_secret = (
        fingerprint_key.get_secret_value()
        if isinstance(fingerprint_key, SecretStr)
        else fingerprint_key
    )
    idempotent_targets = set(notification_secret_values(idempotent_webhooks))

    def request_idempotency_key(channel: str, target: str) -> str | None:
        if not fingerprint_secret:
            return None
        fingerprint = notification_target_fingerprint(target, fingerprint_secret)
        payload_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        return hashlib.sha256(
            f"manual-v1:{channel}:{fingerprint}:{payload_hash}".encode()
        ).hexdigest()
    classified = classify_notification_targets(targets)
    webhook_targets = classified.webhooks
    slack_targets = normalize_slack_webhook_targets([*classified.slack_webhooks, *slack_webhooks])
    pagerduty_keys = normalize_pagerduty_routing_keys(
        [*classified.pagerduty_routing_keys, *pagerduty_routing_keys]
    )

    validation_errors = 0
    validation_failures_by_channel = {"webhook": 0, "slack": 0}
    if allowed_hosts is not None:
        valid_webhooks: list[str] = []
        for target in webhook_targets:
            try:
                valid_webhooks.append(
                    validate_notification_https_target(target, allowed_hosts)
                )
            except ValueError:
                validation_errors += 1
                validation_failures_by_channel["webhook"] += 1
        webhook_targets = valid_webhooks
        valid_slack: list[str] = []
        for target in slack_targets:
            try:
                validated = validate_notification_https_target(target, allowed_hosts)
                parsed = urlsplit(validated)
                if parsed.hostname != "hooks.slack.com" or not parsed.path.startswith(
                    "/services/"
                ):
                    raise ValueError("invalid Slack webhook target")
                valid_slack.append(validated)
            except ValueError:
                validation_errors += 1
                validation_failures_by_channel["slack"] += 1
        slack_targets = valid_slack

    if not webhook_targets and not slack_targets and not pagerduty_keys:
        result = base_notification_result(max_attempts)
        if validation_errors:
            result["attempted"] = validation_errors
            result["failed"] = validation_errors
            result["errors"] = ["target_policy_rejected"] * validation_errors
            for channel, count in validation_failures_by_channel.items():
                result["channels"][channel]["attempted"] = count
                result["channels"][channel]["failed"] = count
        return result

    attempted = 0
    succeeded = 0
    failed = 0
    errors: list[str] = []
    channel_stats = {
        "webhook": {"attempted": 0, "succeeded": 0, "failed": 0},
        "slack": {"attempted": 0, "succeeded": 0, "failed": 0},
        "pagerduty": {"attempted": 0, "succeeded": 0, "failed": 0},
    }
    for channel, count in validation_failures_by_channel.items():
        channel_stats[channel]["attempted"] = count
        channel_stats[channel]["failed"] = count

    failed += validation_errors
    attempted += validation_errors
    errors.extend(["target_policy_rejected"] * validation_errors)

    for target in webhook_targets:
            attempted += 1
            channel_stats["webhook"]["attempted"] += 1
            idempotency_key = (
                request_idempotency_key("webhook", target)
                if target in idempotent_targets
                else None
            )
            delivered, error = await _post_json_with_retry(
                target=target,
                body=payload,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                idempotency_supported=idempotency_key is not None,
                timeout_seconds=timeout_seconds,
                headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
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
                target=target,
                body=slack_payload,
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                idempotency_supported=False,
                timeout_seconds=timeout_seconds,
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
            idempotency_key = request_idempotency_key("pagerduty", routing_key)
            delivered, error = await _post_json_with_retry(
                target=PAGERDUTY_EVENTS_V2_URL,
                body=_pagerduty_payload_with_dedup(
                    payload,
                    routing_key,
                    dedup_key=idempotency_key,
                ),
                max_attempts=max_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                idempotency_supported=idempotency_key is not None,
                timeout_seconds=timeout_seconds,
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
