"""Tests for the core tracing module."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from src.tracing.context import TraceContext, get_current_context, set_current_context, reset_context
from src.tracing.types import TraceType, SpanType, TraceStatus, SpanStatus


class TestTraceContext:
    """Tests for TraceContext."""

    def test_create_context(self):
        """Test creating a trace context."""
        trace_id = uuid4()
        correlation_id = uuid4()

        ctx = TraceContext(
            trace_id=trace_id,
            correlation_id=correlation_id,
            idea_id=123,
            tags=("tag1", "tag2"),
        )

        assert ctx.trace_id == trace_id
        assert ctx.correlation_id == correlation_id
        assert ctx.idea_id == 123
        assert ctx.tags == ("tag1", "tag2")
        assert ctx.parent_span_id is None

    def test_child_context(self):
        """Test creating child context."""
        trace_id = uuid4()
        correlation_id = uuid4()
        span_id = uuid4()

        parent = TraceContext(trace_id=trace_id, correlation_id=correlation_id)
        child = parent.child_context(span_id)

        assert child.trace_id == trace_id
        assert child.correlation_id == correlation_id
        assert child.parent_span_id == span_id

    def test_child_context_preserves_observability_dimensions(self):
        """Test child context keeps org/deployment/session dimensions."""
        parent = TraceContext(
            trace_id=uuid4(),
            correlation_id=uuid4(),
            org_id="acme",
            deployment_id=uuid4(),
            session_id=uuid4(),
            agent_id="worker-1",
        )
        child = parent.child_context(uuid4())

        assert child.org_id == parent.org_id
        assert child.deployment_id == parent.deployment_id
        assert child.session_id == parent.session_id
        assert child.agent_id == parent.agent_id

    def test_context_serialization(self):
        """Test context to/from dict."""
        trace_id = uuid4()
        correlation_id = uuid4()
        deployment_id = uuid4()
        session_id = uuid4()

        ctx = TraceContext(
            trace_id=trace_id,
            correlation_id=correlation_id,
            idea_id=42,
            org_id="acme",
            deployment_id=deployment_id,
            session_id=session_id,
            agent_id="orchestrator",
            tags=("test",),
        )

        data = ctx.to_dict()
        restored = TraceContext.from_dict(data)

        assert restored.trace_id == trace_id
        assert restored.correlation_id == correlation_id
        assert restored.idea_id == 42
        assert restored.org_id == "acme"
        assert restored.deployment_id == deployment_id
        assert restored.session_id == session_id
        assert restored.agent_id == "orchestrator"
        assert restored.tags == ("test",)

    def test_context_var_propagation(self):
        """Test context variable get/set."""
        # Initially no context
        assert get_current_context() is None

        # Set context
        ctx = TraceContext(trace_id=uuid4(), correlation_id=uuid4())
        token = set_current_context(ctx)

        assert get_current_context() == ctx

        # Reset context
        reset_context(token)
        assert get_current_context() is None


class TestTraceTypes:
    """Tests for trace type enums."""

    def test_trace_type_values(self):
        """Test TraceType enum values."""
        assert TraceType.EMBEDDING.value == "embedding"
        assert TraceType.RANKING.value == "ranking"
        assert TraceType.SWOT_ANALYSIS.value == "swot_analysis"

    def test_span_type_values(self):
        """Test SpanType enum values."""
        assert SpanType.LLM_CALL.value == "llm_call"
        assert SpanType.EMBEDDING_CALL.value == "embedding_call"
        assert SpanType.SCORE_CALCULATION.value == "score_calculation"

    def test_status_values(self):
        """Test status enum values."""
        assert TraceStatus.RUNNING.value == "running"
        assert TraceStatus.COMPLETED.value == "completed"
        assert TraceStatus.FAILED.value == "failed"

        assert SpanStatus.RUNNING.value == "running"
        assert SpanStatus.COMPLETED.value == "completed"
        assert SpanStatus.FAILED.value == "failed"


class TestTraceContextImmutability:
    """Test that TraceContext is immutable."""

    def test_context_is_frozen(self):
        """Test that context cannot be modified."""
        ctx = TraceContext(trace_id=uuid4(), correlation_id=uuid4())

        with pytest.raises(FrozenInstanceError):
            ctx.idea_id = 123  # type: ignore
