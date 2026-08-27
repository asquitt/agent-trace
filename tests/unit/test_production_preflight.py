"""Unit tests for production preflight checks."""

from src.config import Settings
from src.services.production_preflight import run_preflight


def test_preflight_flags_missing_hardening_controls() -> None:
    settings = Settings(
        api_auth_enabled=False,
        api_keys=[],
        api_require_tenant_header=False,
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
    assert any("Rate limiting should be enabled" in message for message in failures)
    assert any("Shutdown actions should require approval" in message for message in failures)
    assert any("Scheduler requires explicit org scope" in message for message in failures)


def test_preflight_passes_for_hardened_configuration() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:pass@db:5432/ai_trace",
        redis_url="redis://redis:6379/0",
        api_auth_enabled=True,
        api_keys=["topsecret:platform-bot:admin:*"],
        api_require_tenant_header=True,
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


def test_preflight_rejects_scheduler_notifications_without_durable_outbox() -> None:
    settings = Settings(observability_scheduler_enable_notifications=True)

    failures = [result.message for result in run_preflight(settings) if result.status == "fail"]

    assert any("durable outbox delivery" in message for message in failures)
