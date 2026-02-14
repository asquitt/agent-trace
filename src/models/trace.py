"""Trace models - comprehensive AI decision chain traceability."""

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import UUID, uuid4

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .idea import Idea
    from .observability import AgentDeployment, AgentSession
    from .ranking import Ranking


class TraceType(str, Enum):
    """Type of AI operation being traced."""

    EMBEDDING = "embedding"
    RANKING = "ranking"
    SWOT_ANALYSIS = "swot_analysis"
    SIMILARITY_SEARCH = "similarity_search"
    DIGEST_GENERATION = "digest_generation"
    RECOMMENDATION = "recommendation"


class TraceStatus(str, Enum):
    """Status of trace execution."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class SpanType(str, Enum):
    """Type of operation within a span."""

    LLM_CALL = "llm_call"
    EMBEDDING_CALL = "embedding_call"
    VECTOR_SEARCH = "vector_search"
    PROMPT_TEMPLATE = "prompt_template"
    RESPONSE_PARSE = "response_parse"
    CACHE_HIT = "cache_hit"
    RETRY = "retry"
    SCORE_CALCULATION = "score_calculation"


class SpanStatus(str, Enum):
    """Status of span execution."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class AITrace(Base):
    """Root trace record for an AI operation chain.

    A trace represents a complete AI operation (e.g., ranking an idea)
    and contains multiple spans representing individual API calls.
    """

    __tablename__ = "ai_traces"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Hierarchical tracing
    parent_trace_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    correlation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    # Classification
    trace_type: Mapped[TraceType] = mapped_column(
        PG_ENUM(
            "embedding", "ranking", "swot_analysis", "similarity_search",
            "digest_generation", "recommendation",
            name="trace_type", create_type=False
        ),
        nullable=False,
    )
    status: Mapped[TraceStatus] = mapped_column(
        PG_ENUM(
            "running", "completed", "failed", "timeout",
            name="trace_status", create_type=False
        ),
        default=TraceStatus.RUNNING,
        nullable=False,
    )

    # Linked entities
    idea_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ideas.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ranking_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("rankings.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Execution context
    worker_id: Mapped[Optional[str]] = mapped_column(String(255))
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(255))
    deployment_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_deployments.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # Timing
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column()
    duration_ms: Mapped[Optional[int]] = mapped_column()

    # Aggregated token usage
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Error handling
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    error_type: Mapped[Optional[str]] = mapped_column(String(255))
    error_traceback: Mapped[Optional[str]] = mapped_column(Text)

    # Flexible metadata
    trace_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)
    tags: Mapped[Optional[list[str]]] = mapped_column(JSONB, default=list)

    # Relationships
    spans: Mapped[list["AITraceSpan"]] = relationship(
        "AITraceSpan",
        back_populates="trace",
        cascade="all, delete-orphan",
        order_by="AITraceSpan.started_at",
    )
    parent: Mapped[Optional["AITrace"]] = relationship(
        "AITrace",
        remote_side=[id],
        foreign_keys=[parent_trace_id],
    )
    idea: Mapped[Optional["Idea"]] = relationship("Idea", back_populates="traces")
    ranking: Mapped[Optional["Ranking"]] = relationship("Ranking", back_populates="trace")
    deployment: Mapped[Optional["AgentDeployment"]] = relationship("AgentDeployment", back_populates="traces")
    session: Mapped[Optional["AgentSession"]] = relationship(
        "AgentSession",
        back_populates="traces",
        foreign_keys=[session_id],
    )

    __table_args__ = (
        Index("ix_ai_traces_type_status", "trace_type", "status"),
        Index("ix_ai_traces_started_at", "started_at"),
        Index("ix_ai_traces_correlation_created", "correlation_id", "created_at"),
        Index("ix_ai_traces_org_started", "org_id", "started_at"),
        Index("ix_ai_traces_deployment_started", "deployment_id", "started_at"),
    )

    def __repr__(self) -> str:
        trace_type = self.trace_type.value if hasattr(self.trace_type, "value") else str(self.trace_type)
        status = self.status.value if hasattr(self.status, "value") else str(self.status)
        return f"<AITrace(id={str(self.id)[:8]}, type={trace_type}, status={status})>"


class AITraceSpan(Base):
    """Individual operation span within a trace.

    A span represents a single AI API call (LLM, embedding, etc.)
    with full input/output capture for debugging and auditing.
    """

    __tablename__ = "ai_trace_spans"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    trace_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_traces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_span_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_trace_spans.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Classification
    span_type: Mapped[SpanType] = mapped_column(
        PG_ENUM(
            "llm_call", "embedding_call", "vector_search", "prompt_template",
            "response_parse", "cache_hit", "retry", "score_calculation",
            name="span_type", create_type=False
        ),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Model info
    provider: Mapped[Optional[str]] = mapped_column(String(50))  # anthropic, openai
    model: Mapped[Optional[str]] = mapped_column(String(100))
    model_version: Mapped[Optional[str]] = mapped_column(String(100))

    # Full prompt/response capture (for LLM calls)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)
    user_prompt: Mapped[Optional[str]] = mapped_column(Text)
    assistant_response: Mapped[Optional[str]] = mapped_column(Text)

    # Structured input/output (for any span type)
    input_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    output_data: Mapped[Optional[dict]] = mapped_column(JSONB)

    # Token usage
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # Timing
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column()
    duration_ms: Mapped[Optional[int]] = mapped_column()

    # Status
    status: Mapped[SpanStatus] = mapped_column(
        PG_ENUM("running", "completed", "failed", name="span_status", create_type=False),
        default=SpanStatus.RUNNING,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Additional context
    span_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, default=dict)

    # Relationships
    trace: Mapped["AITrace"] = relationship("AITrace", back_populates="spans")
    session: Mapped[Optional["AgentSession"]] = relationship(
        "AgentSession",
        back_populates="spans",
        foreign_keys=[session_id],
    )
    parent_span: Mapped[Optional["AITraceSpan"]] = relationship(
        "AITraceSpan",
        remote_side=[id],
        foreign_keys=[parent_span_id],
    )
    reasoning_steps: Mapped[list["AITraceReasoning"]] = relationship(
        "AITraceReasoning",
        back_populates="span",
        cascade="all, delete-orphan",
        order_by="AITraceReasoning.step_number",
    )

    __table_args__ = (
        Index("ix_ai_trace_spans_trace_started", "trace_id", "started_at"),
        Index("ix_ai_trace_spans_provider_model", "provider", "model"),
        Index("ix_ai_trace_spans_session_started", "session_id", "started_at"),
    )

    def __repr__(self) -> str:
        span_type = self.span_type.value if hasattr(self.span_type, "value") else str(self.span_type)
        return f"<AITraceSpan(id={str(self.id)[:8]}, name='{self.name}', type={span_type})>"


class AITraceReasoning(Base):
    """Intermediate reasoning steps within a span.

    Captures the "why" behind AI decisions - score calculations,
    SWOT extractions, comparisons, etc.
    """

    __tablename__ = "ai_trace_reasoning"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)

    span_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_trace_spans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Reasoning sequence
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Content
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_context: Mapped[Optional[dict]] = mapped_column(JSONB)
    output_result: Mapped[Optional[dict]] = mapped_column(JSONB)
    confidence: Mapped[Optional[float]] = mapped_column(Float)

    # For score calculations
    dimension: Mapped[Optional[str]] = mapped_column(String(50))
    raw_score: Mapped[Optional[float]] = mapped_column(Float)
    weighted_score: Mapped[Optional[float]] = mapped_column(Float)
    weight_applied: Mapped[Optional[float]] = mapped_column(Float)

    # Plain-English explanation (for customer-facing audit)
    explanation: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    span: Mapped["AITraceSpan"] = relationship("AITraceSpan", back_populates="reasoning_steps")

    __table_args__ = (Index("ix_ai_trace_reasoning_span_step", "span_id", "step_number"),)

    def __repr__(self) -> str:
        return f"<AITraceReasoning(step={self.step_number}, type='{self.step_type}')>"


class AITraceMetrics(Base):
    """Pre-aggregated metrics for fast dashboard queries.

    Hourly aggregations of trace data for efficient analytics.
    """

    __tablename__ = "ai_trace_metrics"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    # Time bucket (hourly aggregation)
    bucket_start: Mapped[datetime] = mapped_column(nullable=False, index=True)
    bucket_end: Mapped[datetime] = mapped_column(nullable=False)

    # Dimensions
    trace_type: Mapped[TraceType] = mapped_column(
        PG_ENUM(
            "embedding", "ranking", "swot_analysis", "similarity_search",
            "digest_generation", "recommendation",
            name="trace_type", create_type=False
        ),
        nullable=False,
    )
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    model: Mapped[Optional[str]] = mapped_column(String(100))

    # Counts
    total_traces: Mapped[int] = mapped_column(Integer, default=0)
    successful_traces: Mapped[int] = mapped_column(Integer, default=0)
    failed_traces: Mapped[int] = mapped_column(Integer, default=0)

    # Token metrics
    total_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    avg_input_tokens: Mapped[float] = mapped_column(Float, default=0.0)
    avg_output_tokens: Mapped[float] = mapped_column(Float, default=0.0)

    # Latency metrics (milliseconds)
    avg_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    min_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    max_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    p50_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    p95_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    p99_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)

    # Cost
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    __table_args__ = (
        Index("ix_ai_trace_metrics_bucket_type", "bucket_start", "trace_type"),
        Index("ix_ai_trace_metrics_provider_model", "provider", "model", "bucket_start"),
    )

    def __repr__(self) -> str:
        trace_type = self.trace_type.value if hasattr(self.trace_type, "value") else str(self.trace_type)
        return f"<AITraceMetrics(bucket={self.bucket_start}, type={trace_type})>"
