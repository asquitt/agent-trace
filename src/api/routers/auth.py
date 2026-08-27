"""Browser-session authentication endpoints."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, SecretStr

from ...database import async_session_factory
from ...dependencies import AuthDep, SettingsDep
from ...security import AuthContext, authenticate_api_key_token
from ...services.browser_sessions import (
    BrowserSessionAuthenticationError,
    create_browser_session,
    revoke_browser_session,
)

router = APIRouter(prefix="/api/v1/auth/browser", tags=["browser-auth"])


class BrowserSessionCreateRequest(BaseModel):
    api_key: SecretStr


class BrowserSessionResponse(BaseModel):
    subject: str
    roles: list[str] = Field(default_factory=list)
    org_ids: list[str] = Field(default_factory=list)
    authentication_method: str
    expires_at: datetime
    csrf_token: str


def _response_from_auth(auth: AuthContext, *, csrf_token: str) -> BrowserSessionResponse:
    if auth.authentication_method != "browser_session" or auth.browser_session_expires_at is None:
        raise HTTPException(status_code=401, detail="Browser session required")
    return BrowserSessionResponse(
        subject=auth.subject,
        roles=sorted(auth.roles),
        org_ids=sorted(auth.org_ids),
        authentication_method=auth.authentication_method,
        expires_at=auth.browser_session_expires_at,
        csrf_token=csrf_token,
    )


def _set_session_cookies(
    response: Response,
    *,
    token: str,
    csrf_token: str,
    settings: SettingsDep,
) -> None:
    max_age = settings.browser_session_ttl_minutes * 60
    response.set_cookie(
        key=settings.browser_session_cookie_name,
        value=token,
        max_age=max_age,
        path="/",
        secure=settings.browser_session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        key=settings.browser_csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        path="/",
        secure=settings.browser_session_cookie_secure,
        httponly=False,
        samesite="strict",
    )


def _clear_session_cookies(response: Response, settings: SettingsDep) -> None:
    response.delete_cookie(
        key=settings.browser_session_cookie_name,
        path="/",
        secure=settings.browser_session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
    response.delete_cookie(
        key=settings.browser_csrf_cookie_name,
        path="/",
        secure=settings.browser_session_cookie_secure,
        httponly=False,
        samesite="strict",
    )


@router.post("/sessions", response_model=BrowserSessionResponse, status_code=201)
async def create_session(
    payload: BrowserSessionCreateRequest,
    settings: SettingsDep,
) -> Response:
    """Exchange an existing API key for an opaque browser session."""
    if not settings.api_auth_enabled:
        raise HTTPException(status_code=503, detail="Browser authentication is unavailable")
    try:
        api_auth = authenticate_api_key_token(payload.api_key.get_secret_value(), settings)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            raise HTTPException(status_code=401, detail="Invalid credentials") from None
        raise

    async with async_session_factory() as db:
        created = await create_browser_session(
            db,
            subject=api_auth.subject,
            roles=api_auth.roles,
            org_ids=api_auth.org_ids,
            settings=settings,
        )

    body = BrowserSessionResponse(
        subject=created.row.subject,
        roles=list(created.row.roles),
        org_ids=list(created.row.org_ids),
        authentication_method="browser_session",
        expires_at=created.row.expires_at,
        csrf_token=created.csrf_token,
    )
    response = JSONResponse(status_code=201, content=body.model_dump(mode="json"))
    _set_session_cookies(
        response,
        token=created.token,
        csrf_token=created.csrf_token,
        settings=settings,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/session", response_model=BrowserSessionResponse)
async def get_session(
    request: Request,
    response: Response,
    auth: AuthDep,
    settings: SettingsDep,
) -> BrowserSessionResponse:
    """Return the current browser principal without exposing session secrets."""
    csrf_token = request.cookies.get(settings.browser_csrf_cookie_name)
    if not csrf_token:
        raise HTTPException(status_code=401, detail="Invalid or expired browser session")
    response.headers["Cache-Control"] = "no-store"
    return _response_from_auth(auth, csrf_token=csrf_token)


@router.delete("/session", status_code=204)
async def delete_session(
    request: Request,
    auth: AuthDep,
    settings: SettingsDep,
) -> Response:
    """Revoke the current browser session and clear its cookies."""
    if auth.authentication_method != "browser_session" or auth.browser_session_id is None:
        raise HTTPException(status_code=401, detail="Browser session required")
    token = request.cookies.get(settings.browser_session_cookie_name)
    if not token:
        raise HTTPException(status_code=401, detail="Browser session required")

    try:
        async with async_session_factory() as db:
            await revoke_browser_session(
                db,
                session_id=auth.browser_session_id,
                token=token,
            )
    except BrowserSessionAuthenticationError:
        raise HTTPException(status_code=401, detail="Invalid or expired browser session") from None

    response = Response(status_code=204)
    _clear_session_cookies(response, settings)
    response.headers["Cache-Control"] = "no-store"
    return response
