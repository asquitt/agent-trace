"""Decorators for easy tracing instrumentation.

These decorators provide a simple way to add tracing to existing functions
without requiring explicit context management.
"""

from functools import wraps
from typing import Callable, Optional, ParamSpec, TypeVar

from .context import get_current_context
from .types import SpanType, TraceType

P = ParamSpec("P")
T = TypeVar("T")


def traced(
    trace_type: TraceType,
    *,
    name: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator to automatically trace an async function.

    Creates a new trace for the decorated function. Use this on top-level
    operations that represent complete AI workflows.

    Args:
        trace_type: Type of operation being traced
        name: Optional name override (defaults to function name)
        tags: Optional tags for filtering

    Example:
        @traced(TraceType.RANKING)
        async def rank_idea(idea: Idea) -> Ranking:
            # This entire function will be traced
            ...
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            # Get tracer from kwargs or dependency injection
            tracer = kwargs.pop("_tracer", None)
            if not tracer:
                from ..dependencies import get_tracer

                tracer = get_tracer()

            # Try to extract idea_id from various sources
            idea_id = _extract_idea_id(args, kwargs)

            async with tracer.start_trace(
                trace_type,
                idea_id=idea_id,
                tags=tags,
                metadata={"function": func.__name__, "module": func.__module__},
            ):
                return await func(*args, **kwargs)  # type: ignore[return-value]

        return wrapper  # type: ignore[return-value]

    return decorator


def traced_span(
    span_type: SpanType,
    *,
    name: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator to create a span within an existing trace.

    Use this on functions that represent individual operations within
    a larger traced workflow.

    Args:
        span_type: Type of operation
        name: Optional name override (defaults to function name)
        provider: AI provider name
        model: Model identifier

    Example:
        @traced_span(SpanType.LLM_CALL, provider="anthropic", model="claude-sonnet-4")
        async def generate_swot(idea: Idea) -> SWOTResult:
            # This will create a span within the active trace
            ...
    """

    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            ctx = get_current_context()
            if not ctx:
                # No trace active, just run the function
                return await func(*args, **kwargs)  # type: ignore[return-value]

            tracer = kwargs.pop("_tracer", None)
            if not tracer:
                from ..dependencies import get_tracer

                tracer = get_tracer()

            span_name = name or func.__name__

            async with tracer.start_span(
                span_type,
                span_name,
                provider=provider,
                model=model,
                metadata={"function": func.__name__},
            ):
                return await func(*args, **kwargs)  # type: ignore[return-value]

        return wrapper  # type: ignore[return-value]

    return decorator


def _extract_idea_id(args: tuple, kwargs: dict) -> Optional[int]:
    """Try to extract idea_id from function arguments."""
    # Check kwargs first
    if "idea_id" in kwargs:
        return kwargs["idea_id"]

    # Check for idea object
    if "idea" in kwargs:
        idea = kwargs["idea"]
        if hasattr(idea, "id"):
            return idea.id  # type: ignore[no-any-return]

    # Check positional args for objects with id
    for arg in args:
        if hasattr(arg, "id") and hasattr(arg, "name"):  # Duck-typing for Idea
            return arg.id  # type: ignore[no-any-return]

    return None
