"""Tests for notification helper functions."""

from src.services.notifications import (
    collect_policy_notification_targets,
    normalize_webhook_targets,
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
