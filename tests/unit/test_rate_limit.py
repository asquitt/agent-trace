"""Unit tests for in-memory rate limiter."""

from src.rate_limit import InMemoryRateLimiter


def test_rate_limiter_allows_within_limit() -> None:
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
    first = limiter.allow("caller", now_epoch=10.0)
    second = limiter.allow("caller", now_epoch=11.0)

    assert first.allowed
    assert second.allowed
    assert second.remaining == 0


def test_rate_limiter_blocks_after_limit() -> None:
    limiter = InMemoryRateLimiter(limit=2, window_seconds=60)
    limiter.allow("caller", now_epoch=10.0)
    limiter.allow("caller", now_epoch=11.0)
    denied = limiter.allow("caller", now_epoch=12.0)

    assert not denied.allowed
    assert denied.retry_after_seconds > 0


def test_rate_limiter_resets_after_window() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=10)
    limiter.allow("caller", now_epoch=10.0)
    denied = limiter.allow("caller", now_epoch=11.0)
    allowed_again = limiter.allow("caller", now_epoch=21.1)

    assert not denied.allowed
    assert allowed_again.allowed


def test_rate_limiter_prunes_stale_keys_on_allow() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=10, cleanup_interval_seconds=1)
    limiter.allow("caller-a", now_epoch=10.0)
    limiter.allow("caller-b", now_epoch=10.1)
    limiter.allow("caller-c", now_epoch=21.5)

    snapshot = limiter.snapshot(now_epoch=21.5)
    assert snapshot["active_keys"] == 1


def test_rate_limiter_snapshot_prunes_stale_keys() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=10, cleanup_interval_seconds=100)
    limiter.allow("caller-a", now_epoch=5.0)

    snapshot = limiter.snapshot(now_epoch=20.5)
    assert snapshot["active_keys"] == 0
