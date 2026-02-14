"""Datetime normalization helpers shared across API and services."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import overload


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


@overload
def to_naive_utc(value: None) -> None: ...


@overload
def to_naive_utc(value: datetime) -> datetime: ...


@overload
def to_naive_utc(value: str) -> datetime: ...


def to_naive_utc(value: datetime | str | None) -> datetime | None:
    """Normalize value into UTC without tzinfo for TIMESTAMP WITHOUT TIME ZONE columns."""
    if value is None:
        return None
    parsed = _parse_datetime(value)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def utc_now_naive() -> datetime:
    """Current UTC timestamp normalized for naive DB timestamp columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_now_iso() -> str:
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
