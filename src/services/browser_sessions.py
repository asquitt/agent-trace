"""Creation, validation, and revocation of opaque browser sessions."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import timedelta
from secrets import token_urlsafe
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..models.auth import BrowserSession
from ..security import AuthContext
from ..utils.time import utc_now_naive


class BrowserSessionAuthenticationError(Exception):
    """Raised when an opaque browser session cannot be authenticated."""


class BrowserSessionCsrfError(Exception):
    """Raised when a cookie-authenticated mutation fails CSRF validation."""


@dataclass(frozen=True)
class CreatedBrowserSession:
    """One-time raw credentials returned only to the login response."""

    row: BrowserSession
    token: str
    csrf_token: str


def hash_browser_secret(value: str) -> str:
    """Return the storage-safe digest for a high-entropy browser secret."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def browser_csrf_is_valid(
    *,
    stored_hash: str,
    cookie_token: str | None,
    header_token: str | None,
) -> bool:
    """Validate a double-submit CSRF token without data-dependent equality checks."""
    cookie_value = cookie_token or ""
    header_value = header_token or ""
    tokens_match = hmac.compare_digest(cookie_value, header_value)
    digest_matches = hmac.compare_digest(hash_browser_secret(header_value), stored_hash)
    return bool(cookie_value and header_value and tokens_match and digest_matches)


async def create_browser_session(
    db: AsyncSession,
    *,
    subject: str,
    roles: frozenset[str],
    org_ids: frozenset[str],
    settings: Settings,
) -> CreatedBrowserSession:
    """Persist hashed credentials and return the raw one-time browser secrets."""
    token = token_urlsafe(32)
    csrf_token = token_urlsafe(32)
    now = utc_now_naive()
    row = BrowserSession(
        token_hash=hash_browser_secret(token),
        csrf_token_hash=hash_browser_secret(csrf_token),
        subject=subject,
        roles=sorted(roles),
        org_ids=sorted(org_ids),
        expires_at=now + timedelta(minutes=settings.browser_session_ttl_minutes),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return CreatedBrowserSession(row=row, token=token, csrf_token=csrf_token)


async def authenticate_browser_session(
    db: AsyncSession,
    *,
    token: str,
    requested_org_id: str | None,
    require_csrf: bool,
    csrf_cookie: str | None,
    csrf_header: str | None,
) -> AuthContext:
    """Resolve an unexpired, unrevoked browser session into an auth context."""
    row = (
        await db.execute(
            select(BrowserSession).where(
                BrowserSession.token_hash == hash_browser_secret(token)
            )
        )
    ).scalar_one_or_none()
    now = utc_now_naive()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise BrowserSessionAuthenticationError

    if require_csrf and not browser_csrf_is_valid(
        stored_hash=row.csrf_token_hash,
        cookie_token=csrf_cookie,
        header_token=csrf_header,
    ):
        raise BrowserSessionCsrfError

    return AuthContext(
        subject=row.subject,
        roles=frozenset(row.roles),
        org_ids=frozenset(row.org_ids),
        auth_enabled=True,
        requested_org_id=requested_org_id,
        authentication_method="browser_session",
        browser_session_id=row.id,
        browser_session_expires_at=row.expires_at,
    )


async def revoke_browser_session(
    db: AsyncSession,
    *,
    session_id: UUID,
    token: str,
) -> None:
    """Revoke the exact browser session represented by the current cookie."""
    row = await db.get(BrowserSession, session_id)
    if row is None or not hmac.compare_digest(row.token_hash, hash_browser_secret(token)):
        raise BrowserSessionAuthenticationError
    if row.revoked_at is None:
        row.revoked_at = utc_now_naive()
        await db.commit()
