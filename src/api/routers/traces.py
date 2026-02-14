"""Trace API endpoints for querying and exporting AI decision chains."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ...dependencies import AuthDep, StorageDep
from ...models.trace import TraceStatus, TraceType
from ...security import AuthContext, require_org_access, require_roles

router = APIRouter(prefix="/api/v1/traces", tags=["traces"])


# Response models


class ReasoningStepResponse(BaseModel):
    """Reasoning step in a span."""

    id: str
    step_number: int
    step_type: str
    description: str
    dimension: Optional[str] = None
    raw_score: Optional[float] = None
    weighted_score: Optional[float] = None
    weight_applied: Optional[float] = None
    explanation: Optional[str] = None
    confidence: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class SpanResponse(BaseModel):
    """Individual span within a trace."""

    id: str
    span_type: str
    name: str
    provider: Optional[str] = None
    model: Optional[str] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0
    status: str
    error_message: Optional[str] = None
    # Optional full content (only if include_prompts=True)
    system_prompt: Optional[str] = None
    user_prompt: Optional[str] = None
    assistant_response: Optional[str] = None
    # Reasoning steps
    reasoning_steps: list[ReasoningStepResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TraceResponse(BaseModel):
    """Full trace with spans."""

    id: str
    correlation_id: str
    trace_type: str
    status: str
    idea_id: Optional[int] = None
    ranking_id: Optional[int] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    error_message: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    spans: list[SpanResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TraceListItem(BaseModel):
    """Trace summary for list view."""

    id: str
    correlation_id: str
    trace_type: str
    status: str
    idea_id: Optional[int] = None
    started_at: datetime
    duration_ms: Optional[int] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    span_count: int = 0
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class TraceListResponse(BaseModel):
    """Paginated trace list."""

    traces: list[TraceListItem]
    total: int
    page: int
    page_size: int
    has_more: bool


class MetricsSummaryResponse(BaseModel):
    """Summary metrics for dashboard."""

    total_traces: int
    successful_traces: int
    failed_traces: int
    total_input_tokens: int
    total_output_tokens: int
    estimated_total_cost_usd: float
    avg_duration_ms: float


# Endpoints


def _require_trace_viewer(auth: AuthContext) -> None:
    require_roles(auth, "viewer", "operator", "admin")


def _resolve_trace_org_scope(auth: AuthContext, org_id: Optional[str]) -> Optional[str]:
    if auth.requested_org_id:
        if org_id and org_id != auth.requested_org_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Request org mismatch. Header requested {auth.requested_org_id}, "
                    f"endpoint requested {org_id}"
                ),
            )
        org_id = auth.requested_org_id

    if org_id:
        require_org_access(auth, org_id)
        return org_id

    if auth.is_global_admin:
        return None

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="org_id is required for non-global-admin access",
    )


def _to_db_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@router.get("", response_model=TraceListResponse)
async def list_traces(
    storage: StorageDep,
    auth: AuthDep,
    org_id: Optional[str] = Query(None, description="Tenant org ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=100, description="Items per page"),
    trace_type: Optional[TraceType] = Query(None, description="Filter by trace type"),
    status: Optional[TraceStatus] = Query(None, description="Filter by status"),
    idea_id: Optional[int] = Query(None, description="Filter by idea ID"),
    correlation_id: Optional[UUID] = Query(None, description="Filter by correlation ID"),
) -> TraceListResponse:
    """List traces with filtering and pagination.

    Use this endpoint to browse traces, filter by type/status, or find
    all traces related to a specific idea.
    """
    _require_trace_viewer(auth)
    resolved_org_id = _resolve_trace_org_scope(auth, org_id)
    offset = (page - 1) * page_size

    traces = await storage.list_traces(
        org_id=resolved_org_id,
        trace_type=trace_type.value if trace_type else None,
        status=status.value if status else None,
        idea_id=idea_id,
        correlation_id=correlation_id,
        limit=page_size + 1,  # Fetch one extra to check has_more
        offset=offset,
    )

    has_more = len(traces) > page_size
    if has_more:
        traces = traces[:page_size]

    # Get total count
    total = await storage.get_trace_count(
        org_id=resolved_org_id,
        trace_type=trace_type.value if trace_type else None,
        status=status.value if status else None,
        idea_id=idea_id,
        correlation_id=correlation_id,
    )

    items = [
        TraceListItem(
            id=str(t.id),
            correlation_id=str(t.correlation_id),
            trace_type=t.trace_type.value if hasattr(t.trace_type, "value") else str(t.trace_type),
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            idea_id=t.idea_id,
            started_at=t.started_at,
            duration_ms=t.duration_ms,
            total_input_tokens=t.total_input_tokens,
            total_output_tokens=t.total_output_tokens,
            estimated_cost_usd=t.estimated_cost_usd,
            span_count=len(t.spans) if hasattr(t, "spans") and t.spans else 0,
            tags=t.tags or [],
        )
        for t in traces
    ]

    return TraceListResponse(
        traces=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=has_more,
    )


@router.get("/metrics/summary", response_model=MetricsSummaryResponse)
async def get_trace_metrics_summary(
    storage: StorageDep,
    auth: AuthDep,
    org_id: Optional[str] = Query(None, description="Tenant org ID"),
    from_ts: Optional[datetime] = Query(
        None,
        alias="from",
        description="Filter traces started at or after this timestamp (ISO-8601 UTC)",
    ),
    to_ts: Optional[datetime] = Query(
        None,
        alias="to",
        description="Filter traces started at or before this timestamp (ISO-8601 UTC)",
    ),
) -> MetricsSummaryResponse:
    """Get aggregate trace metrics for dashboard and operational rollups."""
    _require_trace_viewer(auth)
    resolved_org_id = _resolve_trace_org_scope(auth, org_id)
    if from_ts and to_ts and from_ts > to_ts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="'from' must be less than or equal to 'to'",
        )
    from_ts_db = _to_db_datetime(from_ts)
    to_ts_db = _to_db_datetime(to_ts)

    summary = await storage.get_trace_metrics_summary(
        org_id=resolved_org_id,
        from_ts=from_ts_db,
        to_ts=to_ts_db,
    )
    return MetricsSummaryResponse(**summary)


@router.get("/{trace_id}", response_model=TraceResponse)
async def get_trace(
    trace_id: UUID,
    storage: StorageDep,
    auth: AuthDep,
    include_prompts: bool = Query(False, description="Include full prompts/responses"),
) -> TraceResponse:
    """Get a single trace with all spans.

    Set include_prompts=True to see full prompt and response text.
    This is useful for debugging but may return large responses.
    """
    _require_trace_viewer(auth)
    trace = await storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace.org_id:
        require_org_access(auth, trace.org_id)
    elif not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trace has no org scope and requires global admin access",
        )

    spans = []
    for span in trace.spans or []:
        span_data = SpanResponse(
            id=str(span.id),
            span_type=span.span_type.value if hasattr(span.span_type, "value") else str(span.span_type),
            name=span.name,
            provider=span.provider,
            model=span.model,
            started_at=span.started_at,
            completed_at=span.completed_at,
            duration_ms=span.duration_ms,
            input_tokens=span.input_tokens,
            output_tokens=span.output_tokens,
            status=span.status.value if hasattr(span.status, "value") else str(span.status),
            error_message=span.error_message,
            reasoning_steps=[
                ReasoningStepResponse(
                    id=str(r.id),
                    step_number=r.step_number,
                    step_type=r.step_type,
                    description=r.description,
                    dimension=r.dimension,
                    raw_score=r.raw_score,
                    weighted_score=r.weighted_score,
                    weight_applied=r.weight_applied,
                    explanation=r.explanation,
                    confidence=r.confidence,
                )
                for r in (span.reasoning_steps or [])
            ],
        )

        if include_prompts:
            span_data.system_prompt = span.system_prompt
            span_data.user_prompt = span.user_prompt
            span_data.assistant_response = span.assistant_response

        spans.append(span_data)

    return TraceResponse(
        id=str(trace.id),
        correlation_id=str(trace.correlation_id),
        trace_type=trace.trace_type.value if hasattr(trace.trace_type, "value") else str(trace.trace_type),
        status=trace.status.value if hasattr(trace.status, "value") else str(trace.status),
        idea_id=trace.idea_id,
        ranking_id=trace.ranking_id,
        started_at=trace.started_at,
        completed_at=trace.completed_at,
        duration_ms=trace.duration_ms,
        total_input_tokens=trace.total_input_tokens,
        total_output_tokens=trace.total_output_tokens,
        estimated_cost_usd=trace.estimated_cost_usd,
        error_message=trace.error_message,
        tags=trace.tags or [],
        metadata=trace.trace_metadata or {},
        spans=spans,
    )


@router.get("/{trace_id}/reasoning", response_model=list[ReasoningStepResponse])
async def get_trace_reasoning(
    trace_id: UUID,
    storage: StorageDep,
    auth: AuthDep,
) -> list[ReasoningStepResponse]:
    """Get all reasoning steps for a trace.

    Returns a flat list of all reasoning steps across all spans,
    useful for understanding the decision chain.
    """
    _require_trace_viewer(auth)
    trace = await storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace.org_id:
        require_org_access(auth, trace.org_id)
    elif not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trace has no org scope and requires global admin access",
        )

    reasoning_steps = []
    for span in trace.spans or []:
        for r in span.reasoning_steps or []:
            reasoning_steps.append(
                ReasoningStepResponse(
                    id=str(r.id),
                    step_number=r.step_number,
                    step_type=r.step_type,
                    description=r.description,
                    dimension=r.dimension,
                    raw_score=r.raw_score,
                    weighted_score=r.weighted_score,
                    weight_applied=r.weight_applied,
                    explanation=r.explanation,
                    confidence=r.confidence,
                )
            )

    return reasoning_steps


@router.get("/idea/{idea_id}/history", response_model=list[TraceListItem])
async def get_idea_trace_history(
    idea_id: int,
    storage: StorageDep,
    auth: AuthDep,
    org_id: Optional[str] = Query(None, description="Tenant org ID"),
) -> list[TraceListItem]:
    """Get all traces for a specific idea.

    Shows the complete history of AI operations on this idea,
    useful for auditing and understanding how rankings evolved.
    """
    _require_trace_viewer(auth)
    resolved_org_id = _resolve_trace_org_scope(auth, org_id)
    traces = await storage.get_traces_for_idea(idea_id, org_id=resolved_org_id)

    return [
        TraceListItem(
            id=str(t.id),
            correlation_id=str(t.correlation_id),
            trace_type=t.trace_type.value if hasattr(t.trace_type, "value") else str(t.trace_type),
            status=t.status.value if hasattr(t.status, "value") else str(t.status),
            idea_id=t.idea_id,
            started_at=t.started_at,
            duration_ms=t.duration_ms,
            total_input_tokens=t.total_input_tokens,
            total_output_tokens=t.total_output_tokens,
            estimated_cost_usd=t.estimated_cost_usd,
            span_count=0,  # Not loaded in list query
            tags=t.tags or [],
        )
        for t in traces
    ]


@router.get("/export/{trace_id}/json")
async def export_trace_json(
    trace_id: UUID,
    storage: StorageDep,
    auth: AuthDep,
    include_prompts: bool = Query(True, description="Include full prompts/responses"),
) -> dict:
    """Export a trace as JSON for external analysis.

    Returns the complete trace data including all spans and reasoning steps.
    """
    _require_trace_viewer(auth)
    trace = await storage.get_trace(trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    if trace.org_id:
        require_org_access(auth, trace.org_id)
    elif not auth.is_global_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trace has no org scope and requires global admin access",
        )

    # Build complete export
    export_data = {
        "trace": {
            "id": str(trace.id),
            "correlation_id": str(trace.correlation_id),
            "trace_type": trace.trace_type.value if hasattr(trace.trace_type, "value") else str(trace.trace_type),
            "status": trace.status.value if hasattr(trace.status, "value") else str(trace.status),
            "idea_id": trace.idea_id,
            "ranking_id": trace.ranking_id,
            "started_at": trace.started_at.isoformat(),
            "completed_at": trace.completed_at.isoformat() if trace.completed_at else None,
            "duration_ms": trace.duration_ms,
            "total_input_tokens": trace.total_input_tokens,
            "total_output_tokens": trace.total_output_tokens,
            "estimated_cost_usd": trace.estimated_cost_usd,
            "error_message": trace.error_message,
            "tags": trace.tags or [],
            "metadata": trace.trace_metadata or {},
        },
        "spans": [],
    }

    for span in trace.spans or []:
        span_data = {
            "id": str(span.id),
            "span_type": span.span_type.value if hasattr(span.span_type, "value") else str(span.span_type),
            "name": span.name,
            "provider": span.provider,
            "model": span.model,
            "started_at": span.started_at.isoformat(),
            "completed_at": span.completed_at.isoformat() if span.completed_at else None,
            "duration_ms": span.duration_ms,
            "input_tokens": span.input_tokens,
            "output_tokens": span.output_tokens,
            "status": span.status.value if hasattr(span.status, "value") else str(span.status),
            "error_message": span.error_message,
            "reasoning_steps": [
                {
                    "step_number": r.step_number,
                    "step_type": r.step_type,
                    "description": r.description,
                    "dimension": r.dimension,
                    "raw_score": r.raw_score,
                    "weighted_score": r.weighted_score,
                    "weight_applied": r.weight_applied,
                    "explanation": r.explanation,
                    "confidence": r.confidence,
                }
                for r in (span.reasoning_steps or [])
            ],
        }

        if include_prompts:
            span_data["system_prompt"] = span.system_prompt
            span_data["user_prompt"] = span.user_prompt
            span_data["assistant_response"] = span.assistant_response

        export_data["spans"].append(span_data)

    return export_data
