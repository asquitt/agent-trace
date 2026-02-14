"""Dependency injection providers for FastAPI."""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request

from .config import Settings, get_settings
from .database import async_session_factory
from .security import AuthContext, authenticate_request
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


def get_auth_context(
    request: Request,
    settings: SettingsDep,
) -> AuthContext:
    """Authenticate incoming request and return auth context."""
    return authenticate_request(request, settings)


# Type aliases for common dependencies
TracerDep = Annotated[Tracer, Depends(get_tracer)]
AnthropicDep = Annotated[TracedAnthropicClient, Depends(get_anthropic_client)]
OpenAIDep = Annotated[TracedOpenAIClient, Depends(get_openai_client)]
StorageDep = Annotated[PostgresStorageBackend, Depends(get_storage_backend)]
AuthDep = Annotated[AuthContext, Depends(get_auth_context)]
