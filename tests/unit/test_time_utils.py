"""Tests for shared datetime normalization helpers."""

from datetime import datetime, timedelta, timezone

from src.utils.time import to_naive_utc, utc_now_iso, utc_now_naive


def test_to_naive_utc_preserves_naive_datetime() -> None:
    value = datetime(2026, 2, 14, 10, 15, 30)
    assert to_naive_utc(value) == value


def test_to_naive_utc_converts_aware_datetime() -> None:
    value = datetime(2026, 2, 14, 10, 15, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert to_naive_utc(value) == datetime(2026, 2, 14, 15, 15, 30)


def test_to_naive_utc_parses_iso_with_z_suffix() -> None:
    assert to_naive_utc("2026-02-14T15:15:30Z") == datetime(2026, 2, 14, 15, 15, 30)


def test_utc_now_helpers_return_expected_shapes() -> None:
    now_naive = utc_now_naive()
    now_iso = utc_now_iso()
    assert now_naive.tzinfo is None
    assert now_iso.endswith("+00:00")
