"""Type definitions for the tracing system."""

from enum import Enum
from typing import TypedDict


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


class TraceData(TypedDict, total=False):
    """Type definition for trace data passed to storage."""

    id: str
    parent_trace_id: str | None
    correlation_id: str
    trace_type: str
    status: str
    idea_id: int | None
    ranking_id: int | None
    worker_id: str | None
    celery_task_id: str | None
    org_id: str | None
    deployment_id: str | None
    session_id: str | None
    agent_id: str | None
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float
    error_message: str | None
    error_type: str | None
    error_traceback: str | None
    metadata: dict
    tags: list[str]


class SpanData(TypedDict, total=False):
    """Type definition for span data passed to storage."""

    id: str
    trace_id: str
    parent_span_id: str | None
    session_id: str | None
    span_type: str
    name: str
    provider: str | None
    model: str | None
    model_version: str | None
    system_prompt: str | None
    user_prompt: str | None
    assistant_response: str | None
    input_data: dict | None
    output_data: dict | None
    input_tokens: int
    output_tokens: int
    started_at: str
    completed_at: str | None
    duration_ms: int | None
    status: str
    error_message: str | None
    metadata: dict | None


class ReasoningData(TypedDict, total=False):
    """Type definition for reasoning step data."""

    id: str
    span_id: str
    step_number: int
    step_type: str
    description: str
    input_context: dict | None
    output_result: dict | None
    confidence: float | None
    dimension: str | None
    raw_score: float | None
    weighted_score: float | None
    weight_applied: float | None
    explanation: str | None
