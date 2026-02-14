"""Production readiness preflight checks for deployment settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import Settings


@dataclass(frozen=True)
class CheckResult:
    status: str
    message: str


def _bool_check(predicate: Callable[[], bool], message: str, *, status_on_fail: str = "fail") -> CheckResult:
    if predicate():
        return CheckResult("pass", message)
    return CheckResult(status_on_fail, message)


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
            lambda: (
                len(settings.observability_notification_webhooks) > 0
                or len(settings.observability_notification_slack_webhooks) > 0
                or len(settings.observability_notification_pagerduty_routing_keys) > 0
            ),
            "At least one notification channel should be configured for runtime controls.",
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
    status_counts = {"pass": 0, "warn": 0, "fail": 0}
    for result in results:
        status_counts[result.status] += 1
        print(f"[{result.status.upper()}] {result.message}")
    print(
        f"\nSummary: pass={status_counts['pass']} warn={status_counts['warn']} fail={status_counts['fail']}"
    )
