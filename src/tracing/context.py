"""Trace context propagation using Python contextvars.

This module provides async-safe context propagation for traces,
allowing trace information to flow through async call chains
and into Celery workers.
"""

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID


@dataclass(frozen=True)
class TraceContext:
    """Immutable context propagated through async operations.

    This context carries trace identification and metadata through
    the entire operation chain, enabling correlation of all spans
    within a trace.
    """

    trace_id: UUID
    correlation_id: UUID
    parent_span_id: Optional[UUID] = None
    idea_id: Optional[int] = None
    ranking_id: Optional[int] = None
    worker_id: Optional[str] = None
    celery_task_id: Optional[str] = None
    org_id: Optional[str] = None
    deployment_id: Optional[UUID] = None
    session_id: Optional[UUID] = None
    agent_id: Optional[str] = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def child_context(self, span_id: UUID) -> "TraceContext":
        """Create child context with new parent span.

        Used when entering a nested span to maintain the trace hierarchy.

        Args:
            span_id: The ID of the new parent span.

        Returns:
            New TraceContext with updated parent_span_id.
        """
        return TraceContext(
            trace_id=self.trace_id,
            correlation_id=self.correlation_id,
            parent_span_id=span_id,
            idea_id=self.idea_id,
            ranking_id=self.ranking_id,
            worker_id=self.worker_id,
            celery_task_id=self.celery_task_id,
            org_id=self.org_id,
            deployment_id=self.deployment_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            tags=self.tags,
        )

    def with_idea(self, idea_id: int) -> "TraceContext":
        """Create new context with idea_id set."""
        return TraceContext(
            trace_id=self.trace_id,
            correlation_id=self.correlation_id,
            parent_span_id=self.parent_span_id,
            idea_id=idea_id,
            ranking_id=self.ranking_id,
            worker_id=self.worker_id,
            celery_task_id=self.celery_task_id,
            org_id=self.org_id,
            deployment_id=self.deployment_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            tags=self.tags,
        )

    def with_ranking(self, ranking_id: int) -> "TraceContext":
        """Create new context with ranking_id set."""
        return TraceContext(
            trace_id=self.trace_id,
            correlation_id=self.correlation_id,
            parent_span_id=self.parent_span_id,
            idea_id=self.idea_id,
            ranking_id=ranking_id,
            worker_id=self.worker_id,
            celery_task_id=self.celery_task_id,
            org_id=self.org_id,
            deployment_id=self.deployment_id,
            session_id=self.session_id,
            agent_id=self.agent_id,
            tags=self.tags,
        )

    def to_dict(self) -> dict:
        """Serialize context for task queue propagation."""
        return {
            "trace_id": str(self.trace_id),
            "correlation_id": str(self.correlation_id),
            "parent_span_id": str(self.parent_span_id) if self.parent_span_id else None,
            "idea_id": self.idea_id,
            "ranking_id": self.ranking_id,
            "worker_id": self.worker_id,
            "celery_task_id": self.celery_task_id,
            "org_id": self.org_id,
            "deployment_id": str(self.deployment_id) if self.deployment_id else None,
            "session_id": str(self.session_id) if self.session_id else None,
            "agent_id": self.agent_id,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TraceContext":
        """Deserialize context from task queue headers."""
        return cls(
            trace_id=UUID(data["trace_id"]),
            correlation_id=UUID(data["correlation_id"]),
            parent_span_id=UUID(data["parent_span_id"]) if data.get("parent_span_id") else None,
            idea_id=data.get("idea_id"),
            ranking_id=data.get("ranking_id"),
            worker_id=data.get("worker_id"),
            celery_task_id=data.get("celery_task_id"),
            org_id=data.get("org_id"),
            deployment_id=UUID(data["deployment_id"]) if data.get("deployment_id") else None,
            session_id=UUID(data["session_id"]) if data.get("session_id") else None,
            agent_id=data.get("agent_id"),
            tags=tuple(data.get("tags", [])),
        )


# Context variable for async propagation
_trace_context: ContextVar[Optional[TraceContext]] = ContextVar("trace_context", default=None)


def get_current_context() -> Optional[TraceContext]:
    """Get the current trace context.

    Returns:
        The active TraceContext, or None if no trace is active.
    """
    return _trace_context.get()


def set_current_context(ctx: Optional[TraceContext]) -> Token[Optional[TraceContext]]:
    """Set the current trace context.

    Args:
        ctx: The TraceContext to set, or None to clear.

    Returns:
        A token that can be used to restore the previous context.
    """
    return _trace_context.set(ctx)


def reset_context(token: Token[Optional[TraceContext]]) -> None:
    """Reset context to a previous state using a token.

    Args:
        token: Token returned from set_current_context.
    """
    _trace_context.reset(token)
