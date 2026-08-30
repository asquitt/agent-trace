"""FastAPI application for AI Trace API."""

import hashlib
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ..config import get_settings
from ..database import async_session_factory, close_db, init_db
from ..dependencies import AuthDep
from ..rate_limit import InMemoryRateLimiter
from ..security import require_global_admin
from ..services import ObservabilityOperationsScheduler, public_scheduler_status
from .routers import (
    auth_router,
    console_router,
    observability_router,
    runtime_controls_router,
    traces_router,
)

logger = structlog.get_logger(__name__)
settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Startup
    logger.info("application_startup", service=settings.service_name)
    await init_db()
    app.state.started_at = time.time()
    app.state.request_metrics = {
        "total_requests": 0,
        "in_flight": 0,
        "status_counts": defaultdict(int),
        "path_counts": defaultdict(int),
        "total_duration_ms": 0.0,
    }
    app.state.rate_limiter = (
        InMemoryRateLimiter(
            limit=settings.api_rate_limit_requests_per_window,
            window_seconds=settings.api_rate_limit_window_seconds,
            max_keys=settings.api_rate_limit_max_keys,
        )
        if settings.api_rate_limit_enabled
        else None
    )
    scheduler = ObservabilityOperationsScheduler(async_session_factory, settings)
    app.state.observability_scheduler = scheduler
    await scheduler.start()

    yield

    # Shutdown
    if hasattr(app.state, "observability_scheduler"):
        await app.state.observability_scheduler.stop()
    logger.info("application_shutdown", service=settings.service_name)
    await close_db()


app = FastAPI(
    title="AI Trace API",
    description="Agent observability and runtime governance control plane",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS middleware for dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(auth_router)
app.include_router(traces_router)
app.include_router(observability_router)
app.include_router(runtime_controls_router)
app.include_router(console_router)


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    start = time.perf_counter()
    metrics = request.app.state.request_metrics
    metrics["total_requests"] += 1
    metrics["in_flight"] += 1
    metrics["path_counts"][request.url.path] += 1
    async def _dispatch_with_rate_limit() -> Response:
        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is None:
            return await call_next(request)

        principal = request.headers.get(settings.api_key_header) or request.headers.get("Authorization")
        if not principal:
            browser_token = request.cookies.get(settings.browser_session_cookie_name)
            principal = (
                f"browser:{hashlib.sha256(browser_token.encode('utf-8')).hexdigest()}"
                if browser_token
                else "anonymous"
            )
        tenant = request.headers.get(settings.api_tenant_header, "-")
        key = f"{principal}:{tenant}"
        if settings.api_rate_limit_per_path:
            key = f"{key}:{request.url.path}"
        decision = limiter.allow(key)
        if not decision.allowed:
            blocked = JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded",
                    "limit": decision.limit,
                    "retry_after_seconds": decision.retry_after_seconds,
                },
            )
            blocked.headers["Retry-After"] = str(max(int(decision.retry_after_seconds), 1))
            blocked.headers["X-RateLimit-Limit"] = str(decision.limit)
            blocked.headers["X-RateLimit-Remaining"] = "0"
            blocked.headers["X-RateLimit-Reset"] = str(int(decision.reset_at_epoch))
            return blocked

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(decision.limit)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-RateLimit-Reset"] = str(int(decision.reset_at_epoch))
        return response

    try:
        response = await _dispatch_with_rate_limit()
    except Exception as exc:
        if settings.trace_capture_prompts is True:
            metrics["status_counts"][500] += 1
            logger.exception(
                "request_failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
            )
            raise

        logger.error(
            "request_failed",
            error_code="request_processing_failed",
            exception_type=type(exc).__name__,
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "error_code": "request_processing_failed",
                "request_id": request_id,
            },
        )
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        metrics["total_duration_ms"] += elapsed_ms
        metrics["in_flight"] = max(metrics["in_flight"] - 1, 0)

    metrics["status_counts"][response.status_code] += 1
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    return response


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    """Liveness check."""
    return {"status": "alive", "service": settings.service_name}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness check that verifies DB reachability."""
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        payload = {"status": "ready", "service": settings.service_name}
        return JSONResponse(status_code=200, content=payload)
    except Exception as exc:
        payload = {"status": "not_ready", "service": settings.service_name, "error": str(exc)}
        return JSONResponse(status_code=503, content=payload)


@app.get("/metrics")
async def get_metrics(request: Request, auth: AuthDep) -> dict[str, Any]:
    """Return cross-tenant operational metrics to global administrators."""
    require_global_admin(auth)
    metrics = request.app.state.request_metrics
    uptime_seconds = int(time.time() - request.app.state.started_at)
    total = int(metrics["total_requests"])
    avg_duration = float(metrics["total_duration_ms"] / total) if total else 0.0
    scheduler = getattr(request.app.state, "observability_scheduler", None)
    scheduler_snapshot = public_scheduler_status(scheduler)
    return {
        "service": settings.service_name,
        "uptime_seconds": uptime_seconds,
        "requests": {
            "total": total,
            "in_flight": int(metrics["in_flight"]),
            "avg_duration_ms": avg_duration,
            "status_counts": dict(metrics["status_counts"]),
            "top_paths": dict(
                sorted(
                    metrics["path_counts"].items(),
                    key=lambda item: item[1],
                    reverse=True,
                )[:25]
            ),
        },
        "rate_limit": (
            request.app.state.rate_limiter.snapshot()
            if getattr(request.app.state, "rate_limiter", None) is not None
            else {"enabled": False}
        ),
        "scheduler": scheduler_snapshot,
    }


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Backward-compatible health endpoint."""
    return {"status": "healthy", "service": settings.service_name}


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint with API information."""
    return {
        "service": "AI Trace API",
        "version": "0.2.0",
        "description": "Agent observability and runtime governance control plane",
        "docs": "/docs",
        "endpoints": {
            "traces": "/api/v1/traces",
            "observability": "/api/v1/observability",
            "operator_console": "/console/",
            "observability_risk_insights": "/api/v1/observability/insights/risk",
            "observability_policy_simulation": "/api/v1/observability/policies/simulate",
            "observability_operations_status": "/api/v1/observability/operations/status",
            "metrics": "/metrics",
            "health_live": "/health/live",
            "health_ready": "/health/ready",
            "health": "/health",
        },
    }
