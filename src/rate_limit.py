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
        max_keys: int = 10000,
    ) -> None:
        self._limit = max(limit, 1)
        self._window_seconds = max(window_seconds, 1)
        cleanup_default = min(self._window_seconds, 60)
        self._cleanup_interval_seconds = max(cleanup_interval_seconds or cleanup_default, 1)
        self._max_keys = max(max_keys, 1)
        self._next_cleanup_epoch = 0.0
        self._buckets: dict[str, deque[float]] = {}
        self._last_seen_epoch: dict[str, float] = {}
        self._evicted_keys = 0
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
            self._last_seen_epoch.pop(bucket_key, None)

    def _evict_oldest_locked(self) -> None:
        if not self._last_seen_epoch:
            return
        oldest_key = min(self._last_seen_epoch.items(), key=lambda item: item[1])[0]
        self._buckets.pop(oldest_key, None)
        self._last_seen_epoch.pop(oldest_key, None)
        self._evicted_keys += 1

    def allow(self, key: str, now_epoch: float | None = None) -> RateLimitDecision:
        """Evaluate whether a request should be allowed."""
        now = now_epoch if now_epoch is not None else time.time()

        with self._lock:
            if now >= self._next_cleanup_epoch:
                self._prune_stale_locked(now)
                self._next_cleanup_epoch = now + self._cleanup_interval_seconds

            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    self._evict_oldest_locked()
                bucket = deque()
                self._buckets[key] = bucket
            self._last_seen_epoch[key] = now

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
                "max_keys": self._max_keys,
                "evicted_keys": self._evicted_keys,
            }
