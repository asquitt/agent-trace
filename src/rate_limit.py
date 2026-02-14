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

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = max(limit, 1)
        self._window_seconds = max(window_seconds, 1)
        self._buckets: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str, now_epoch: float | None = None) -> RateLimitDecision:
        """Evaluate whether a request should be allowed."""
        now = now_epoch if now_epoch is not None else time.time()
        cutoff = now - self._window_seconds

        with self._lock:
            bucket = self._buckets.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

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

    def snapshot(self) -> dict[str, int]:
        """Expose internal state counts for diagnostics."""
        with self._lock:
            return {
                "active_keys": len(self._buckets),
                "window_seconds": self._window_seconds,
                "limit": self._limit,
            }
