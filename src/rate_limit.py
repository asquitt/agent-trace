"""Simple in-memory rate limiting primitives."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class RateLimitDecision:
    """Decision result for one request."""

    allowed: bool
    limit: int
    remaining: int
    reset_at_epoch: float
    retry_after_seconds: float


class InMemoryRateLimiter:
    """Fixed-window rate limiter keyed by caller identity."""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        cleanup_interval_seconds: int | None = None,
    ) -> None:
        self._limit = max(limit, 1)
        self._window_seconds = max(window_seconds, 1)
        cleanup_default = min(self._window_seconds, 60)
        self._cleanup_interval_seconds = max(cleanup_interval_seconds or cleanup_default, 1)
        self._next_cleanup_epoch = 0.0
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def _prune_stale_locked(self, now: float) -> None:
        cutoff = now - self._window_seconds
        stale_keys: list[str] = []
        for bucket_key, bucket in self._buckets.items():
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                stale_keys.append(bucket_key)
        for bucket_key in stale_keys:
            self._buckets.pop(bucket_key, None)

    def allow(self, key: str, now_epoch: float | None = None) -> RateLimitDecision:
        """Evaluate whether a request should be allowed."""
        now = now_epoch if now_epoch is not None else time.time()

        with self._lock:
            if now >= self._next_cleanup_epoch:
                self._prune_stale_locked(now)
                self._next_cleanup_epoch = now + self._cleanup_interval_seconds

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = deque()
                self._buckets[key] = bucket

            if len(bucket) >= self._limit:
                reset_at = bucket[0] + self._window_seconds
                retry_after = max(reset_at - now, 0.0)
                return RateLimitDecision(
                    allowed=False,
                    limit=self._limit,
                    remaining=0,
                    reset_at_epoch=reset_at,
                    retry_after_seconds=retry_after,
                )

            bucket.append(now)
            remaining = max(self._limit - len(bucket), 0)
            reset_at = bucket[0] + self._window_seconds
            return RateLimitDecision(
                allowed=True,
                limit=self._limit,
                remaining=remaining,
                reset_at_epoch=reset_at,
                retry_after_seconds=max(reset_at - now, 0.0),
            )

    def snapshot(self, now_epoch: float | None = None) -> dict[str, int]:
        """Expose internal state counts for diagnostics."""
        now = now_epoch if now_epoch is not None else time.time()
        with self._lock:
            self._prune_stale_locked(now)
            return {
                "active_keys": len(self._buckets),
                "window_seconds": self._window_seconds,
                "limit": self._limit,
            }
