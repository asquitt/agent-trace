"""API routers."""

from .auth import router as auth_router
from .console import router as console_router
from .observability import router as observability_router
from .traces import router as traces_router

__all__ = ["auth_router", "console_router", "traces_router", "observability_router"]
