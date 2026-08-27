"""Production readiness preflight checks for deployment settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings
from .notifications import (
    notification_claim_window_is_safe,
    notification_secret_values,
    validate_notification_https_target,
)


@dataclass(frozen=True)
class CheckResult:
    status: str
    message: str


def _bool_check(predicate: Callable[[], bool], message: str, *, status_on_fail: str = "fail") -> CheckResult:
    if predicate():
        return CheckResult("pass", message)
    return CheckResult(status_on_fail, message)


def _scheduler_notification_configuration_valid(settings: Settings) -> bool:
    if not settings.observability_scheduler_enable_notifications:
        return True
    if not settings.observability_scheduler_enabled:
        return False
    if len(
        settings.observability_notification_fingerprint_key.get_secret_value().encode("utf-8")
    ) < 32:
        return False
    targets = notification_secret_values(settings.observability_notification_webhooks)
    slack_targets = notification_secret_values(
        settings.observability_notification_slack_webhooks
    )
    pagerduty_keys = notification_secret_values(
        settings.observability_notification_pagerduty_routing_keys
    )
    if not targets and not slack_targets and not pagerduty_keys:
        return False
    if not notification_claim_window_is_safe(
        claim_seconds=settings.observability_notification_claim_seconds,
        attempt_timeout_seconds=settings.observability_notification_timeout_seconds,
    ):
        return False
    try:
        for target in targets:
            validate_notification_https_target(
                target,
                settings.observability_notification_allowed_hosts,
            )
        for target in slack_targets:
            validated = validate_notification_https_target(
                target,
                settings.observability_notification_allowed_hosts,
            )
            from urllib.parse import urlsplit

            parsed = urlsplit(validated)
            if parsed.hostname != "hooks.slack.com" or not parsed.path.startswith("/services/"):
                return False
        for target in notification_secret_values(
            settings.observability_notification_idempotent_webhooks
        ):
            if target not in targets:
                return False
    except ValueError:
        return False
    return all(key.strip() for key in pagerduty_keys)


def run_preflight(settings: Settings) -> list[CheckResult]:
    """Run deployment-focused readiness checks against loaded settings."""
    checks: list[CheckResult] = [
        _bool_check(
            lambda: settings.api_auth_enabled,
            "API auth must be enabled (`API_AUTH_ENABLED=true`).",
        ),
        _bool_check(
            lambda: len(settings.api_keys) > 0,
            "At least one API key must be configured (`API_KEYS`).",
        ),
        _bool_check(
            lambda: settings.api_require_tenant_header,
            "Tenant header enforcement should be enabled (`API_REQUIRE_TENANT_HEADER=true`).",
        ),
        _bool_check(
            lambda: settings.browser_session_cookie_secure,
            "Browser session cookies must require HTTPS (`BROWSER_SESSION_COOKIE_SECURE=true`).",
        ),
        _bool_check(
            lambda: (Path(settings.operator_console_dist_dir) / "index.html").is_file(),
            "Built operator console assets must exist (`OPERATOR_CONSOLE_DIST_DIR/index.html`).",
        ),
        _bool_check(
            lambda: settings.api_rate_limit_enabled,
            "Rate limiting should be enabled (`API_RATE_LIMIT_ENABLED=true`).",
        ),
        _bool_check(
            lambda: settings.api_rate_limit_max_keys >= 1000,
            "Rate limit key capacity should be >= 1000 (`API_RATE_LIMIT_MAX_KEYS`).",
            status_on_fail="warn",
        ),
        _bool_check(
            lambda: settings.observability_shutdown_requires_approval,
            "Shutdown actions should require approval (`OBSERVABILITY_SHUTDOWN_REQUIRES_APPROVAL=true`).",
        ),
        _bool_check(
            lambda: (
                not settings.observability_scheduler_enabled
                or len(settings.observability_scheduler_org_ids) > 0
            ),
            "Scheduler requires explicit org scope when enabled (`OBSERVABILITY_SCHEDULER_ORG_IDS`).",
        ),
        _bool_check(
            lambda: _scheduler_notification_configuration_valid(settings),
            (
                "Scheduler notifications require the durable outbox, an enabled scheduler, "
                "a 32-byte fingerprint key, safe channels, exact HTTPS host allowlisting, "
                "and a claim window covering the total attempt deadline plus finalization margin."
            ),
        ),
        _bool_check(
            lambda: (
                len(settings.observability_notification_webhooks) > 0
                or len(settings.observability_notification_slack_webhooks) > 0
                or len(settings.observability_notification_pagerduty_routing_keys) > 0
            ),
            "At least one notification channel should be configured for manual runtime/SIEM dispatch.",
            status_on_fail="warn",
        ),
        _bool_check(
            lambda: "localhost" not in settings.database_url and "127.0.0.1" not in settings.database_url,
            "Database URL should not point to localhost in production.",
            status_on_fail="warn",
        ),
        _bool_check(
            lambda: "localhost" not in settings.redis_url and "127.0.0.1" not in settings.redis_url,
            "Redis URL should not point to localhost in production.",
            status_on_fail="warn",
        ),
    ]
    return checks


def print_results(results: list[CheckResult]) -> None:
    """Print preflight check results in a compact human-readable form."""
    status_counts = {"ok": 0, "warn": 0, "error": 0}
    for result in results:
        if result.status == "pass":
            status_counts["ok"] += 1
        elif result.status == "warn":
            status_counts["warn"] += 1
        else:
            status_counts["error"] += 1
        print(f"[{result.status.upper()}] {result.message}")
    print(
        f"\nSummary: pass={status_counts['ok']} warn={status_counts['warn']} fail={status_counts['error']}"
    )
