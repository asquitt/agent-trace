"""FastAPI application for AI Trace API."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    logger.info("application_startup", service="ai-trace")
    await init_db()
    scheduler = ObservabilityOperationsScheduler(async_session_factory, settings)
    app.state.observability_scheduler = scheduler
    await scheduler.start()

    yield

    # Shutdown
    if hasattr(app.state, "observability_scheduler"):
        await app.state.observability_scheduler.stop()
    logger.info("application_shutdown", service="ai-trace")
    await close_db()


app = FastAPI(
    title="AI Trace API",
    description="AI Traceability & Explainability System - Audit and visualize AI decision chains",
    version="0.1.0",
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


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "service": "ai-trace"}


@app.get("/")
async def root() -> dict:
    """Root endpoint with API information."""
    return {
        "service": "AI Trace API",
        "version": "0.1.0",
        "description": "AI Traceability & Explainability System",
        "docs": "/docs",
        "endpoints": {
            "traces": "/api/v1/traces",
            "observability": "/api/v1/observability",
            "observability_dashboard_ui": "/api/v1/observability/dashboard/ui",
            "observability_operations_status": "/api/v1/observability/operations/status",
            "health": "/health",
        },
    }
