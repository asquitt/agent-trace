"""Unit tests for production preflight checks."""

from pathlib import Path

from src.config import Settings
from src.services.production_preflight import run_preflight


def test_preflight_flags_missing_hardening_controls(tmp_path: Path) -> None:
    settings = Settings(
        runtime_governance_enabled=True,
        api_auth_enabled=False,
        api_keys=[],
        api_require_tenant_header=False,
        browser_session_cookie_secure=False,
        operator_console_dist_dir=str(tmp_path / "missing"),
        api_rate_limit_enabled=False,
        observability_shutdown_requires_approval=False,
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=[],
    )
    results = run_preflight(settings)

    failures = [result.message for result in results if result.status == "fail"]
    assert any("API auth must be enabled" in message for message in failures)
    assert any("At least one API key must be configured" in message for message in failures)
    assert any("Tenant header enforcement" in message for message in failures)
    assert any("Browser session cookies must require HTTPS" in message for message in failures)
    assert any("Built operator console assets must exist" in message for message in failures)
    assert any("Rate limiting should be enabled" in message for message in failures)
    assert any("Shutdown actions should require approval" in message for message in failures)
    assert any("Scheduler requires explicit org scope" in message for message in failures)


def test_preflight_passes_for_hardened_configuration(tmp_path: Path) -> None:
    console_dist = tmp_path / "console"
    console_dist.mkdir()
    (console_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    settings = Settings(
        runtime_governance_enabled=True,
        database_url="postgresql+asyncpg://user:pass@db:5432/ai_trace",
        redis_url="redis://redis:6379/0",
        api_auth_enabled=True,
        api_keys=["topsecret:platform-bot:admin:*"],
        api_require_tenant_header=True,
        browser_session_cookie_secure=True,
        operator_console_dist_dir=str(console_dist),
        api_rate_limit_enabled=True,
        api_rate_limit_max_keys=5000,
        observability_shutdown_requires_approval=True,
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
        observability_notification_webhooks=["https://hooks.example.com/runtime"],
    )
    results = run_preflight(settings)

    assert not any(result.status == "fail" for result in results)
    assert all(result.status == "pass" for result in results)


def test_preflight_warns_on_low_rate_limit_capacity() -> None:
    settings = Settings(
        api_auth_enabled=True,
        api_keys=["topsecret:platform-bot:admin:*"],
        api_require_tenant_header=True,
        api_rate_limit_enabled=True,
        api_rate_limit_max_keys=500,
    )
    results = run_preflight(settings)

    warnings = [result.message for result in results if result.status == "warn"]
    assert any("Rate limit key capacity" in message for message in warnings)


def test_preflight_rejects_incomplete_scheduler_notification_contract() -> None:
    settings = Settings(observability_scheduler_enable_notifications=True)

    failures = [result.message for result in run_preflight(settings) if result.status == "fail"]

    assert any("durable outbox" in message for message in failures)


def test_preflight_accepts_safe_scheduler_notification_contract(tmp_path: Path) -> None:
    console_dist = tmp_path / "console"
    console_dist.mkdir()
    (console_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    settings = Settings(
        runtime_governance_enabled=True,
        database_url="postgresql+asyncpg://user:pass@db:5432/ai_trace",
        redis_url="redis://redis:6379/0",
        api_auth_enabled=True,
        api_keys=["topsecret:platform-bot:admin:*"],
        api_require_tenant_header=True,
        browser_session_cookie_secure=True,
        operator_console_dist_dir=str(console_dist),
        api_rate_limit_enabled=True,
        observability_shutdown_requires_approval=True,
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
        observability_scheduler_enable_notifications=True,
        observability_notification_webhooks=["https://hooks.example.com/runtime"],
        observability_notification_allowed_hosts=["hooks.example.com"],
        observability_notification_fingerprint_key="x" * 32,
    )

    assert not any(result.status == "fail" for result in run_preflight(settings))


def test_preflight_rejects_claim_without_finalization_margin(tmp_path: Path) -> None:
    console_dist = tmp_path / "console"
    console_dist.mkdir()
    (console_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    settings = Settings(
        runtime_governance_enabled=True,
        operator_console_dist_dir=str(console_dist),
        observability_scheduler_enabled=True,
        observability_scheduler_org_ids=["acme"],
        observability_scheduler_enable_notifications=True,
        observability_notification_webhooks=["https://hooks.example.com/runtime"],
        observability_notification_allowed_hosts=["hooks.example.com"],
        observability_notification_fingerprint_key="x" * 32,
        observability_notification_timeout_seconds=9.9,
        observability_notification_claim_seconds=10,
    )

    failures = [result.message for result in run_preflight(settings) if result.status == "fail"]

    assert any("total attempt deadline plus finalization margin" in message for message in failures)


def test_preflight_rejects_missing_operator_console(tmp_path: Path) -> None:
    settings = Settings(operator_console_dist_dir=str(tmp_path / "missing"))

    failures = [result.message for result in run_preflight(settings) if result.status == "fail"]

    assert any("Built operator console assets must exist" in message for message in failures)


def test_preflight_rejects_stale_autonomy_flags_while_governance_is_frozen() -> None:
    settings = Settings(
        runtime_governance_enabled=False,
        observability_scheduler_enabled=True,
        observability_scheduler_run_policies=True,
        observability_scheduler_execute_policy_actions=True,
    )

    failures = [result.message for result in run_preflight(settings) if result.status == "fail"]

    assert any("Disabled runtime governance" in message for message in failures)
