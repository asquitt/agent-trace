"""Dependency injection providers for FastAPI."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from .config import Settings, get_settings
from .database import async_session_factory
from .security import AuthContext, authenticate_request, extract_api_token
from .services.browser_sessions import (
    BrowserSessionAuthenticationError,
    BrowserSessionCsrfError,
    authenticate_browser_session,
)
from .tracing import Tracer
from .tracing.providers import TracedAnthropicClient, TracedOpenAIClient
from .tracing.storage import PostgresStorageBackend

# Type aliases for dependency injection
SettingsDep = Annotated[Settings, Depends(get_settings)]


@lru_cache
def get_storage_backend() -> PostgresStorageBackend:
    """Get the PostgreSQL storage backend (singleton)."""
    return PostgresStorageBackend(async_session_factory)


@lru_cache
def get_tracer() -> Tracer:
    """Get the tracer instance (singleton)."""
    storage = get_storage_backend()
    settings = get_settings()
    return Tracer(
        storage=storage,
        service_name="ai-trace",
        capture_prompts=settings.trace_capture_prompts,
    )


def get_anthropic_client() -> TracedAnthropicClient:
    """Get a traced Anthropic client."""
    tracer = get_tracer()
    settings = get_settings()
    return TracedAnthropicClient(
        tracer=tracer,
        api_key=settings.anthropic_api_key.get_secret_value(),
    )


def get_openai_client() -> TracedOpenAIClient:
    """Get a traced OpenAI client."""
    tracer = get_tracer()
    settings = get_settings()
    return TracedOpenAIClient(
        tracer=tracer,
        api_key=settings.openai_api_key.get_secret_value(),
    )


async def get_auth_context(
    request: Request,
    settings: SettingsDep,
) -> AuthContext:
    """Authenticate incoming request and return auth context."""
    if not settings.api_auth_enabled or extract_api_token(request, settings.api_key_header):
        return authenticate_request(request, settings)

    is_session_management = request.url.path.startswith("/api/v1/auth/browser/")
    token = request.cookies.get(settings.browser_session_cookie_name)
    if not token:
        if is_session_management:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired browser session",
            )
        return authenticate_request(request, settings)

    requested_org_id = request.headers.get(settings.api_tenant_header)
    if settings.api_require_tenant_header and not requested_org_id and not is_session_management:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{settings.api_tenant_header} header is required",
        )

    require_csrf = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    async with async_session_factory() as db:
        try:
            return await authenticate_browser_session(
                db,
                token=token,
                requested_org_id=requested_org_id,
                require_csrf=require_csrf,
                csrf_cookie=request.cookies.get(settings.browser_csrf_cookie_name),
                csrf_header=request.headers.get(settings.browser_csrf_header),
            )
        except BrowserSessionCsrfError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed",
            ) from exc
        except BrowserSessionAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired browser session",
            ) from exc


# Type aliases for common dependencies
TracerDep = Annotated[Tracer, Depends(get_tracer)]
AnthropicDep = Annotated[TracedAnthropicClient, Depends(get_anthropic_client)]
OpenAIDep = Annotated[TracedOpenAIClient, Depends(get_openai_client)]
StorageDep = Annotated[PostgresStorageBackend, Depends(get_storage_backend)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
