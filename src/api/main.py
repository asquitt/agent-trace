"""FastAPI application for AI Trace API."""

import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from ..config import get_settings
from ..database import close_db, init_db
from ..database import async_session_factory
from ..services import ObservabilityOperationsScheduler
from .routers import observability_router, traces_router

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
app.include_router(traces_router)
app.include_router(observability_router)


@app.middleware("http")
async def request_observability_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    start = time.perf_counter()
    metrics = request.app.state.request_metrics
    metrics["total_requests"] += 1
    metrics["in_flight"] += 1
    metrics["path_counts"][request.url.path] += 1

    try:
        response = await call_next(request)
    except Exception:
        metrics["status_counts"][500] += 1
        logger.exception(
            "request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        raise
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        metrics["total_duration_ms"] += elapsed_ms
        metrics["in_flight"] = max(metrics["in_flight"] - 1, 0)

    metrics["status_counts"][response.status_code] += 1
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.2f}"
    return response


@app.get("/health/live")
async def health_live() -> dict:
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
async def get_metrics(request: Request) -> dict:
    """Simple JSON metrics endpoint for uptime and request telemetry."""
    metrics = request.app.state.request_metrics
    uptime_seconds = int(time.time() - request.app.state.started_at)
    total = int(metrics["total_requests"])
    avg_duration = float(metrics["total_duration_ms"] / total) if total else 0.0
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
    }


@app.get("/health")
async def health_check() -> dict:
    """Backward-compatible health endpoint."""
    return {"status": "healthy", "service": settings.service_name}


@app.get("/")
async def root() -> dict:
    """Root endpoint with API information."""
    return {
        "service": "AI Trace API",
        "version": "0.2.0",
        "description": "Agent observability and runtime governance control plane",
        "docs": "/docs",
        "endpoints": {
            "traces": "/api/v1/traces",
            "observability": "/api/v1/observability",
            "observability_dashboard_ui": "/api/v1/observability/dashboard/ui",
            "observability_operations_status": "/api/v1/observability/operations/status",
            "metrics": "/metrics",
            "health_live": "/health/live",
            "health_ready": "/health/ready",
            "health": "/health",
        },
    }
