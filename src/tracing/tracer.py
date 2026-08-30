"""Core Tracer class for AI operation tracing.

The Tracer provides async context managers for creating traces and spans,
with automatic context propagation and storage integration.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, AsyncGenerator, Optional, Protocol
from uuid import UUID, uuid4

import structlog

from .context import TraceContext, get_current_context, reset_context, set_current_context
from .types import ReasoningData, SpanData, SpanStatus, SpanType, TraceData, TraceStatus, TraceType

if TYPE_CHECKING:
    from contextvars import Token

logger = structlog.get_logger(__name__)


class StorageBackend(Protocol):
    """Protocol for trace storage backends."""

    async def save_trace(self, trace: TraceData) -> None:
        """Save a new trace record."""
        ...

    async def save_span(self, span: SpanData) -> None:
        """Save a new span record."""
        ...

    async def save_reasoning(self, reasoning: ReasoningData) -> None:
        """Save a reasoning step record."""
        ...

    async def update_trace(self, trace_id: UUID, updates: dict[str, Any]) -> None:
        """Update an existing trace."""
        ...

    async def update_span(self, span_id: UUID, updates: dict[str, Any]) -> None:
        """Update an existing span."""
        ...

    async def aggregate_trace_tokens(
        self, trace_id: UUID, input_tokens: int, output_tokens: int, cost: float
    ) -> None:
        """Add token usage to trace totals."""
        ...


@dataclass
class SpanContext:
    """Context for an active span, allowing output recording.

    This is yielded from start_span and allows the caller to record
    outputs, tokens, and reasoning steps during span execution.
    """

    span_id: UUID
    trace_id: UUID
    tracer: "Tracer"
    started_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    _output_data: Optional[dict] = field(default=None, repr=False)
    _response_text: Optional[str] = field(default=None, repr=False)
    _reasoning_steps: list[ReasoningData] = field(default_factory=list, repr=False)

    def record_output(self, data: dict) -> None:
        """Record structured output data for the span."""
        self._output_data = data

    def record_response(self, text: str) -> None:
        """Record LLM response text."""
        self._response_text = text

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    async def record_reasoning(
        self,
        step_type: str,
        description: str,
        *,
        input_context: Optional[dict] = None,
        output_result: Optional[dict] = None,
        confidence: Optional[float] = None,
        dimension: Optional[str] = None,
        raw_score: Optional[float] = None,
        weighted_score: Optional[float] = None,
        weight_applied: Optional[float] = None,
        explanation: Optional[str] = None,
    ) -> None:
        """Record an intermediate reasoning step.

        Args:
            step_type: Type of reasoning (e.g., "score_calculation", "swot_extraction")
            description: Human-readable description of what happened
            input_context: Input data for this step
            output_result: Output/result of this step
            confidence: Confidence score (0-1)
            dimension: For scoring, which dimension was scored
            raw_score: Unweighted score (0-100)
            weighted_score: Score after weight applied
            weight_applied: The weight used
            explanation: Plain-English explanation for customers
        """
        step_number = len(self._reasoning_steps) + 1
        reasoning: ReasoningData = {
            "id": str(uuid4()),
            "span_id": str(self.span_id),
            "step_number": step_number,
            "step_type": step_type,
            "description": description,
        }
        if input_context is not None:
            reasoning["input_context"] = input_context
        if output_result is not None:
            reasoning["output_result"] = output_result
        if confidence is not None:
            reasoning["confidence"] = confidence
        if dimension is not None:
            reasoning["dimension"] = dimension
        if raw_score is not None:
            reasoning["raw_score"] = raw_score
        if weighted_score is not None:
            reasoning["weighted_score"] = weighted_score
        if weight_applied is not None:
            reasoning["weight_applied"] = weight_applied
        if explanation is not None:
            reasoning["explanation"] = explanation

        self._reasoning_steps.append(reasoning)

        # Save immediately to storage
        await self.tracer.storage.save_reasoning(reasoning)


class Tracer:
    """Main tracer for AI operations.

    The Tracer provides async context managers for creating traces and spans.
    All trace data is automatically persisted to the configured storage backend.

    Example:
        async with tracer.start_trace(TraceType.RANKING, idea_id=123) as ctx:
            async with tracer.start_span(SpanType.LLM_CALL, "swot_analysis") as span:
                response = await anthropic.create_message(...)
                span.record_tokens(response.usage.input_tokens, response.usage.output_tokens)
                span.record_response(response.content[0].text)
    """

    def __init__(
        self,
        storage: StorageBackend,
        service_name: str = "ai-trace",
        capture_prompts: bool = False,
    ):
        """Initialize the tracer.

        Args:
            storage: Storage backend for persisting traces
            service_name: Name of the service for identification
            capture_prompts: Whether to capture full prompts/responses
        """
        if type(capture_prompts) is not bool:
            raise TypeError("capture_prompts must be a boolean")
        self.storage = storage
        self.service_name = service_name
        self.capture_prompts = capture_prompts
        self.logger = structlog.get_logger().bind(component="tracer")

    @asynccontextmanager
    async def start_trace(
        self,
        trace_type: TraceType,
        *,
        correlation_id: Optional[UUID] = None,
        idea_id: Optional[int] = None,
        ranking_id: Optional[int] = None,
        worker_id: Optional[str] = None,
        celery_task_id: Optional[str] = None,
        org_id: Optional[str] = None,
        deployment_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        agent_id: Optional[str] = None,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> AsyncGenerator[TraceContext, None]:
        """Start a new trace.

        Creates a new trace record and sets up the trace context for
        all subsequent operations within the async context.

        Args:
            trace_type: Type of operation being traced
            correlation_id: Optional ID to correlate related traces
            idea_id: Optional linked idea ID
            ranking_id: Optional linked ranking ID
            worker_id: Optional worker/process identifier
            celery_task_id: Optional Celery task ID
            org_id: Optional organization identifier
            deployment_id: Optional deployment identifier
            session_id: Optional session identifier
            agent_id: Optional agent identifier
            tags: Optional list of tags for filtering
            metadata: Optional additional metadata

        Yields:
            TraceContext for the active trace
        """
        trace_id = uuid4()
        correlation_id = correlation_id or uuid4()
        started_at = datetime.now(timezone.utc)

        # Check for parent trace
        parent_ctx = get_current_context()
        parent_trace_id = parent_ctx.trace_id if parent_ctx else None

        # Create trace record
        trace_data: TraceData = {
            "id": str(trace_id),
            "parent_trace_id": str(parent_trace_id) if parent_trace_id else None,
            "correlation_id": str(correlation_id),
            "trace_type": trace_type.value,
            "status": TraceStatus.RUNNING.value,
            "idea_id": idea_id,
            "ranking_id": ranking_id,
            "worker_id": worker_id,
            "celery_task_id": celery_task_id,
            "org_id": org_id,
            "deployment_id": str(deployment_id) if deployment_id else None,
            "session_id": str(session_id) if session_id else None,
            "agent_id": agent_id,
            "started_at": started_at.isoformat(),
            "metadata": metadata or {},
            "tags": tags or [],
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

        await self.storage.save_trace(trace_data)

        self.logger.info(
            "trace_started",
            trace_id=str(trace_id),
            trace_type=trace_type.value,
            idea_id=idea_id,
        )

        # Set context
        ctx = TraceContext(
            trace_id=trace_id,
            correlation_id=correlation_id,
            idea_id=idea_id,
            ranking_id=ranking_id,
            worker_id=worker_id,
            celery_task_id=celery_task_id,
            org_id=org_id,
            deployment_id=deployment_id,
            session_id=session_id,
            agent_id=agent_id,
            tags=tuple(tags or []),
        )

        token: Token[Optional[TraceContext]] = set_current_context(ctx)

        try:
            yield ctx

            # Mark completed
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            await self.storage.update_trace(
                trace_id,
                {
                    "status": TraceStatus.COMPLETED.value,
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": duration_ms,
                },
            )

            self.logger.info(
                "trace_completed",
                trace_id=str(trace_id),
                duration_ms=duration_ms,
            )

        except Exception as e:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            import traceback

            error_type = type(e).__name__
            if self.capture_prompts is True:
                error_message = str(e)
                error_traceback = traceback.format_exc()
            else:
                error_message = f"trace_execution_failed:{error_type}"
                error_traceback = None

            await self.storage.update_trace(
                trace_id,
                {
                    "status": TraceStatus.FAILED.value,
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                    "error_type": error_type,
                    "error_traceback": error_traceback,
                },
            )

            self.logger.error(
                "trace_failed",
                trace_id=str(trace_id),
                error=error_message,
                error_type=error_type,
            )
            raise

        finally:
            reset_context(token)

    @asynccontextmanager
    async def start_span(
        self,
        span_type: SpanType,
        name: str,
        *,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        model_version: Optional[str] = None,
        system_prompt: Optional[str] = None,
        user_prompt: Optional[str] = None,
        input_data: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> AsyncGenerator[SpanContext, None]:
        """Start a new span within the current trace.

        Creates a span record for an individual operation (e.g., an LLM call)
        within the active trace.

        Args:
            span_type: Type of operation
            name: Human-readable name for the span
            provider: AI provider (e.g., "anthropic", "openai")
            model: Model identifier
            model_version: Model version if applicable
            system_prompt: System prompt (if capturing prompts)
            user_prompt: User prompt (if capturing prompts)
            input_data: Structured input data
            metadata: Additional metadata

        Yields:
            SpanContext for recording outputs and tokens

        Raises:
            RuntimeError: If no active trace context exists
        """
        ctx = get_current_context()
        if not ctx:
            raise RuntimeError("No active trace context. Call start_trace first.")

        span_id = uuid4()
        started_at = datetime.now(timezone.utc)

        # Build span data
        span_data: SpanData = {
            "id": str(span_id),
            "trace_id": str(ctx.trace_id),
            "parent_span_id": str(ctx.parent_span_id) if ctx.parent_span_id else None,
            "session_id": str(ctx.session_id) if ctx.session_id else None,
            "span_type": span_type.value,
            "name": name,
            "provider": provider,
            "model": model,
            "model_version": model_version,
            "started_at": started_at.isoformat(),
            "status": SpanStatus.RUNNING.value,
            "input_tokens": 0,
            "output_tokens": 0,
            "metadata": metadata or {},
        }

        # Capture prompts if enabled
        if self.capture_prompts is True:
            if system_prompt:
                span_data["system_prompt"] = system_prompt
            if user_prompt:
                span_data["user_prompt"] = user_prompt
        if input_data:
            span_data["input_data"] = input_data

        await self.storage.save_span(span_data)

        # Create child context for nested spans
        child_ctx = ctx.child_context(span_id)
        token = set_current_context(child_ctx)

        span_context = SpanContext(
            span_id=span_id,
            trace_id=ctx.trace_id,
            tracer=self,
            started_at=started_at,
        )

        try:
            yield span_context

            # Calculate duration
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            # Build update data
            update_data: dict[str, Any] = {
                "status": SpanStatus.COMPLETED.value,
                "completed_at": completed_at.isoformat(),
                "duration_ms": duration_ms,
                "input_tokens": span_context.input_tokens,
                "output_tokens": span_context.output_tokens,
            }

            if span_context._output_data:
                update_data["output_data"] = span_context._output_data
            if span_context._response_text and self.capture_prompts is True:
                update_data["assistant_response"] = span_context._response_text

            await self.storage.update_span(span_id, update_data)

            # Aggregate tokens to trace
            if span_context.input_tokens > 0 or span_context.output_tokens > 0:
                cost = self._calculate_cost(
                    provider or "",
                    model or "",
                    span_context.input_tokens,
                    span_context.output_tokens,
                )
                await self.storage.aggregate_trace_tokens(
                    ctx.trace_id,
                    span_context.input_tokens,
                    span_context.output_tokens,
                    cost,
                )

        except Exception as e:
            completed_at = datetime.now(timezone.utc)
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            error_type = type(e).__name__
            error_message = (
                str(e)
                if self.capture_prompts is True
                else f"span_execution_failed:{error_type}"
            )

            await self.storage.update_span(
                span_id,
                {
                    "status": SpanStatus.FAILED.value,
                    "completed_at": completed_at.isoformat(),
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                },
            )
            self.logger.error(
                "span_failed",
                trace_id=str(ctx.trace_id),
                span_id=str(span_id),
                error=error_message,
                error_type=error_type,
            )
            raise

        finally:
            reset_context(token)

    def _calculate_cost(
        self, provider: str, model: str, input_tokens: int, output_tokens: int
    ) -> float:
        """Calculate estimated cost in USD.

        Uses approximate pricing for common models.
        """
        # Claude Sonnet pricing
        if provider == "anthropic" and "sonnet" in model.lower():
            input_cost = (input_tokens / 1_000_000) * 3.00
            output_cost = (output_tokens / 1_000_000) * 15.00
            return input_cost + output_cost

        # Claude Haiku pricing
        if provider == "anthropic" and "haiku" in model.lower():
            input_cost = (input_tokens / 1_000_000) * 0.25
            output_cost = (output_tokens / 1_000_000) * 1.25
            return input_cost + output_cost

        # Claude Opus pricing
        if provider == "anthropic" and "opus" in model.lower():
            input_cost = (input_tokens / 1_000_000) * 15.00
            output_cost = (output_tokens / 1_000_000) * 75.00
            return input_cost + output_cost

        # OpenAI embedding pricing
        if provider == "openai" and "embedding" in model.lower():
            return (input_tokens / 1_000_000) * 0.02

        # Default: assume similar to Sonnet
        input_cost = (input_tokens / 1_000_000) * 3.00
        output_cost = (output_tokens / 1_000_000) * 15.00
        return input_cost + output_cost
