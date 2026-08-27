"""Contract tests for environment-backed application settings."""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_shipped_env_example_loads_with_local_compose_contract() -> None:
    """The documented clean-clone environment must be directly loadable."""
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=PROJECT_ROOT / ".env.example")

    assert settings.database_url.endswith("@localhost:5434/ai_trace")
    assert settings.api_keys == ["replace-me:platform-admin:viewer|operator|admin:*"]
    assert settings.cors_origins == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    assert settings.observability_scheduler_org_ids == ["acme"]
    assert settings.observability_active_session_inactivity_minutes == 30
    assert settings.observability_notification_webhooks == []
    assert settings.observability_notification_slack_webhooks == []
    assert settings.observability_notification_pagerduty_routing_keys == []

    compose = (PROJECT_ROOT / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
    assert '- "5434:5432"' in compose


def test_list_settings_accept_scalar_comma_and_json_environment_values() -> None:
    environment = {
        "API_KEYS": (
            "first:service-a:viewer:acme,"
            "second:service-b:operator:contoso"
        ),
        "CORS_ORIGINS": '["https://app.example.com", "https://admin.example.com"]',
        "OBSERVABILITY_SCHEDULER_ORG_IDS": "prod-org",
        "OBSERVABILITY_NOTIFICATION_WEBHOOKS": (
            "https://hooks.example.com/primary,https://hooks.example.com/fallback"
        ),
        "OBSERVABILITY_NOTIFICATION_SLACK_WEBHOOKS": "",
        "OBSERVABILITY_NOTIFICATION_PAGERDUTY_ROUTING_KEYS": "[]",
    }

    with patch.dict(os.environ, environment, clear=True):
        settings = Settings(_env_file=None)

    assert settings.api_keys == [
        "first:service-a:viewer:acme",
        "second:service-b:operator:contoso",
    ]
    assert settings.cors_origins == [
        "https://app.example.com",
        "https://admin.example.com",
    ]
    assert settings.observability_scheduler_org_ids == ["prod-org"]
    assert settings.observability_notification_webhooks == [
        "https://hooks.example.com/primary",
        "https://hooks.example.com/fallback",
    ]
    assert settings.observability_notification_slack_webhooks == []
    assert settings.observability_notification_pagerduty_routing_keys == []


def test_kubernetes_scheduler_scalar_is_a_valid_list_setting() -> None:
    config_map = (PROJECT_ROOT / "deploy" / "k8s" / "runtime-configmap.yaml").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^\s*scheduler_org_ids:\s*"([^"]+)"\s*$', config_map, re.MULTILINE)
    assert match is not None

    with patch.dict(
        os.environ,
        {"OBSERVABILITY_SCHEDULER_ORG_IDS": match.group(1)},
        clear=True,
    ):
        settings = Settings(_env_file=None)

    assert settings.observability_scheduler_org_ids == ["prod-org"]


def test_active_session_inactivity_threshold_defaults_and_validates() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert Settings(_env_file=None).observability_active_session_inactivity_minutes == 30

        assert (
            Settings(
                _env_file=None,
                observability_active_session_inactivity_minutes=90,
            ).observability_active_session_inactivity_minutes
            == 90
        )

        for invalid_value in (0, 10081):
            with pytest.raises(ValidationError):
                Settings(
                    _env_file=None,
                    observability_active_session_inactivity_minutes=invalid_value,
                )
