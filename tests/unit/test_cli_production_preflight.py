"""Tests for production preflight CLI entrypoint."""

from src.cli import production_preflight as preflight_cli
from src.services.production_preflight import CheckResult


def test_main_returns_success_when_no_failures(monkeypatch) -> None:
    monkeypatch.setattr(preflight_cli, "Settings", lambda: object())
    monkeypatch.setattr(
        preflight_cli,
        "run_preflight",
        lambda _settings: [
            CheckResult(status="pass", message="ok"),
            CheckResult(status="warn", message="warn only"),
        ],
    )
    monkeypatch.setattr(preflight_cli, "print_results", lambda _results: None)

    assert preflight_cli.main() == 0


def test_main_returns_failure_when_any_check_fails(monkeypatch) -> None:
    monkeypatch.setattr(preflight_cli, "Settings", lambda: object())
    monkeypatch.setattr(
        preflight_cli,
        "run_preflight",
        lambda _settings: [
            CheckResult(status="pass", message="ok"),
            CheckResult(status="fail", message="auth missing"),
        ],
    )
    monkeypatch.setattr(preflight_cli, "print_results", lambda _results: None)

    assert preflight_cli.main() == 1
