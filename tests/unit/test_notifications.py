"""Tests for notification helper functions."""

from typing import Any

import pytest

from src.services import notifications as notifications_service
from src.services.notifications import (
    PAGERDUTY_EVENTS_V2_URL,
    base_notification_result,
    classify_notification_targets,
    collect_policy_notification_target_strings,
    collect_policy_notification_targets,
    normalize_webhook_targets,
    runtime_event_severity,
    send_runtime_notifications,
    severity_rank,
    skipped_notification_result,
)


def test_normalize_webhook_targets_filters_and_deduplicates() -> None:
    targets = [
        " https://hooks.example.com/a ",
        "http://hooks.example.com/b",
        "mailto:test@example.com",
        "",
        "https://hooks.example.com/a",
    ]
    normalized = normalize_webhook_targets(targets)
    assert normalized == [
        "http://hooks.example.com/b",
        "https://hooks.example.com/a",
    ]


def test_collect_policy_notification_targets_only_for_breaches() -> None:
    summary = {
        "results": [
            {
                "policy_id": "p1",
                "breaches": [{"trigger_type": "max_cost_usd"}],
                "notification_targets": ["https://hooks.example.com/a"],
            },
            {
                "policy_id": "p2",
                "breaches": [],
                "notification_targets": ["https://hooks.example.com/ignored"],
            },
            {
                "policy_id": "p3",
                "breaches": [{"trigger_type": "max_actions"}],
                "notification_targets": ["http://hooks.example.com/b"],
            },
        ]
    }
    targets = collect_policy_notification_targets(summary)
    assert targets == [
        "http://hooks.example.com/b",
        "https://hooks.example.com/a",
    ]


def test_collect_policy_notification_target_strings_keeps_channel_prefixes() -> None:
    summary = {
        "results": [
            {
                "policy_id": "p1",
                "breaches": [{"trigger_type": "max_cost_usd"}],
                "notification_targets": [
                    "slack:https://hooks.slack.com/services/T000/B000/AAA",
                    "pagerduty:pd-routing-key",
                    "https://hooks.example.com/a",
                ],
            },
            {
                "policy_id": "p2",
                "breaches": [],
                "notification_targets": ["pagerduty:ignore"],
            },
        ]
    }
    targets = collect_policy_notification_target_strings(summary)
    assert set(targets) == {
        "pagerduty:pd-routing-key",
        "slack:https://hooks.slack.com/services/T000/B000/AAA",
        "https://hooks.example.com/a",
    }


def test_classify_notification_targets_splits_channels() -> None:
    target_set = classify_notification_targets(
        [
            "https://hooks.example.com/base",
            "slack:https://hooks.slack.com/services/T000/B000/AAA",
            "https://hooks.slack.com/services/T111/B111/BBB",
            "pagerduty:pd-routing-key",
        ]
    )
    assert target_set.webhooks == ["https://hooks.example.com/base"]
    assert target_set.slack_webhooks == [
        "https://hooks.slack.com/services/T000/B000/AAA",
        "https://hooks.slack.com/services/T111/B111/BBB",
    ]
    assert target_set.pagerduty_routing_keys == ["pd-routing-key"]


def test_runtime_event_severity_prefers_policy_shutdown() -> None:
    severity = runtime_event_severity(
        {
            "policy_summary": {
                "breached_policies": 1,
                "results": [{"action_result": {"action": "shutdown"}}],
            },
            "detector_summary": {"created_anomalies": 0},
        }
    )
    assert severity == "critical"


def test_runtime_event_severity_uses_detector_volume() -> None:
    severity = runtime_event_severity(
        {
            "policy_summary": {"breached_policies": 0, "results": []},
            "detector_summary": {"created_anomalies": 2},
        }
    )
    assert severity == "warning"


def test_severity_rank_orders_levels() -> None:
    assert severity_rank("info") < severity_rank("warning")
    assert severity_rank("warning") < severity_rank("error")
    assert severity_rank("error") < severity_rank("critical")


def test_base_notification_result_defaults() -> None:
    result = base_notification_result(0)
    assert result["attempted"] == 0
    assert result["max_attempts"] == 1
    assert result["channels"]["webhook"]["attempted"] == 0


def test_skipped_notification_result_includes_skip_metadata() -> None:
    result = skipped_notification_result(
        max_attempts=3,
        reason="below_min_severity",
        event_severity="info",
        min_severity="warning",
    )
    assert result["skipped"] is True
    assert result["skip_reason"] == "below_min_severity"
    assert result["event_severity"] == "info"
    assert result["min_severity"] == "warning"


@pytest.mark.asyncio
async def test_send_runtime_notifications_routes_all_channels(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_post_json_with_retry(
        _client: Any,
        *,
        target: str,
        body: dict[str, Any],
        max_attempts: int,
        retry_backoff_seconds: float,
    ) -> tuple[bool, str | None]:
        assert max_attempts == 2
        assert retry_backoff_seconds == 0.25
        calls.append((target, body))
        return True, None

    monkeypatch.setattr(notifications_service, "_post_json_with_retry", fake_post_json_with_retry)

    payload = {
        "event_type": "observability_runtime_event",
        "org_id": "acme",
        "detector_summary": {"created_anomalies": 2},
        "policy_summary": {"breached_policies": 1, "results": []},
    }
    result = await send_runtime_notifications(
        [
            "https://hooks.example.com/runtime",
            "slack:https://hooks.slack.com/services/T000/B000/AAA",
            "pagerduty:pd-policy-key",
        ],
        payload,
        slack_webhooks=["https://hooks.slack.com/services/T111/B111/BBB"],
        pagerduty_routing_keys=["pd-global-key"],
        max_attempts=2,
        retry_backoff_seconds=0.25,
    )

    assert result["attempted"] == 5
    assert result["succeeded"] == 5
    assert result["failed"] == 0
    assert result["channels"]["webhook"]["attempted"] == 1
    assert result["channels"]["slack"]["attempted"] == 2
    assert result["channels"]["pagerduty"]["attempted"] == 2

    webhook_calls = [body for target, body in calls if target == "https://hooks.example.com/runtime"]
    assert len(webhook_calls) == 1
    assert webhook_calls[0]["org_id"] == "acme"

    slack_calls = [
        body
        for target, body in calls
        if target.startswith("https://hooks.slack.com/services/")
    ]
    assert len(slack_calls) == 2
    assert all("text" in body for body in slack_calls)

    pagerduty_calls = [body for target, body in calls if target == PAGERDUTY_EVENTS_V2_URL]
    assert len(pagerduty_calls) == 2
    assert sorted(body["routing_key"] for body in pagerduty_calls) == [
        "pd-global-key",
        "pd-policy-key",
    ]


@pytest.mark.asyncio
async def test_send_runtime_notifications_ignores_invalid_targets() -> None:
    result = await send_runtime_notifications(
        ["mailto:alerts@example.com", "ftp://example.com/hook"],
        {"event_type": "observability_runtime_event", "org_id": "acme"},
        slack_webhooks=[],
        pagerduty_routing_keys=[],
    )
    assert result["attempted"] == 0
    assert result["succeeded"] == 0
    assert result["failed"] == 0
