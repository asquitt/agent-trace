"""Authentication and authorization helpers for API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from fastapi import HTTPException, Request, status

from .config import Settings

SUPPORTED_ROLES = {"viewer", "operator", "admin"}


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context."""

    subject: str
    roles: frozenset[str]
    org_ids: frozenset[str]
    auth_enabled: bool
    requested_org_id: str | None = None

    @property
    def is_global_admin(self) -> bool:
        return "admin" in self.roles and "*" in self.org_ids

    def has_any_role(self, roles: Iterable[str]) -> bool:
        return bool(self.roles.intersection(set(roles)))

    def can_access_org(self, org_id: str) -> bool:
        return "*" in self.org_ids or org_id in self.org_ids


@dataclass(frozen=True)
class ApiKeyRecord:
    """Parsed API key record."""

    token: str
    subject: str
    roles: frozenset[str]
    org_ids: frozenset[str]


def _extract_token(request: Request, key_header: str) -> str | None:
    raw = request.headers.get(key_header)
    if raw:
        return raw.strip()
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return None


def _parse_record(entry: str) -> ApiKeyRecord:
    parts = [part.strip() for part in entry.split(":")]
    if len(parts) != 4:
        raise ValueError(
            "Invalid API key entry format. Expected token:subject:role1|role2:org1|org2"
        )
    token, subject, roles_raw, orgs_raw = parts
    if not token:
        raise ValueError("API key token must not be empty")
    if not subject:
        raise ValueError("API key subject must not be empty")

    roles = {r.strip() for r in roles_raw.split("|") if r.strip()}
    if not roles:
        raise ValueError(f"API key entry for {subject} has no roles")
    invalid_roles = roles.difference(SUPPORTED_ROLES)
    if invalid_roles:
        raise ValueError(f"Unsupported role(s) for {subject}: {sorted(invalid_roles)}")

    org_ids = {o.strip() for o in orgs_raw.split("|") if o.strip()}
    if not org_ids:
        raise ValueError(f"API key entry for {subject} has no org scopes")

    return ApiKeyRecord(
        token=token,
        subject=subject,
        roles=frozenset(roles),
        org_ids=frozenset(org_ids),
    )


@lru_cache
def _api_key_index(entries: tuple[str, ...]) -> dict[str, ApiKeyRecord]:
    index: dict[str, ApiKeyRecord] = {}
    for entry in entries:
        record = _parse_record(entry)
        index[record.token] = record
    return index


def authenticate_request(request: Request, settings: Settings) -> AuthContext:
    """Authenticate request and build an authorization context."""
    requested_org_id = request.headers.get(settings.api_tenant_header)

    if not settings.api_auth_enabled:
        return AuthContext(
            subject="local-development",
            roles=frozenset({"viewer", "operator", "admin"}),
            org_ids=frozenset({"*"}),
            auth_enabled=False,
            requested_org_id=requested_org_id,
        )

    if settings.api_require_tenant_header and not requested_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{settings.api_tenant_header} header is required",
        )

    token = _extract_token(request, settings.api_key_header)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
        )

    try:
        record = _api_key_index(tuple(settings.api_keys)).get(token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid API key configuration: {exc}",
        ) from exc

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return AuthContext(
        subject=record.subject,
        roles=record.roles,
        org_ids=record.org_ids,
        auth_enabled=True,
        requested_org_id=requested_org_id,
    )


def require_roles(auth: AuthContext, *roles: str) -> None:
    """Raise if caller does not have one of the required roles."""
    required = set(roles)
    if not auth.has_any_role(required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires one of roles: {sorted(required)}",
        )


def require_org_access(auth: AuthContext, org_id: str) -> None:
    """Raise if caller cannot access a tenant org."""
    if auth.requested_org_id and auth.requested_org_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Request org mismatch. Header requested {auth.requested_org_id}, "
                f"endpoint requested {org_id}"
            ),
        )

    if not auth.can_access_org(org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied for org_id={org_id}",
        )
