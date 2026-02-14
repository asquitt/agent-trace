"""PostgreSQL storage backend for trace persistence.

This module implements the StorageBackend protocol using PostgreSQL
with async SQLAlchemy for high-performance trace storage.
"""

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

import structlog
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ...models.trace import AITrace, AITraceReasoning, AITraceSpan, TraceStatus
from ..types import ReasoningData, SpanData, TraceData

logger = structlog.get_logger(__name__)


def _to_db_datetime(value: str | datetime) -> datetime:
    """Normalize ISO or datetime input to naive UTC for DB columns."""
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class PostgresStorageBackend:
    """PostgreSQL storage backend for traces.

    Implements the StorageBackend protocol for persisting traces,
    spans, and reasoning steps to PostgreSQL.

    Example:
        storage = PostgresStorageBackend(async_session_factory)
        tracer = Tracer(storage)

        async with tracer.start_trace(TraceType.RANKING):
            # Traces are automatically persisted
            ...
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """Initialize the storage backend.

        Args:
            session_factory: SQLAlchemy async session factory
        """
        self.session_factory = session_factory
        self.logger = logger.bind(storage="postgres")

    async def save_trace(self, trace: TraceData) -> None:
        """Save a new trace record.

        Args:
            trace: Trace data to persist
        """
        async with self.session_factory() as session:
            db_trace = AITrace(
                id=UUID(trace["id"]),
                parent_trace_id=UUID(trace["parent_trace_id"]) if trace.get("parent_trace_id") else None,
                correlation_id=UUID(trace["correlation_id"]),
                trace_type=trace["trace_type"],
                status=trace["status"],
                idea_id=trace.get("idea_id"),
                ranking_id=trace.get("ranking_id"),
                worker_id=trace.get("worker_id"),
                celery_task_id=trace.get("celery_task_id"),
                org_id=trace.get("org_id"),
                deployment_id=UUID(trace["deployment_id"]) if trace.get("deployment_id") else None,
                session_id=UUID(trace["session_id"]) if trace.get("session_id") else None,
                agent_id=trace.get("agent_id"),
                started_at=_to_db_datetime(trace["started_at"]),
                total_input_tokens=trace.get("total_input_tokens", 0),
                total_output_tokens=trace.get("total_output_tokens", 0),
                estimated_cost_usd=trace.get("estimated_cost_usd", 0.0),
                trace_metadata=trace.get("metadata", {}),
                tags=trace.get("tags", []),
            )
            session.add(db_trace)
            await session.commit()

            self.logger.debug("trace_saved", trace_id=trace["id"])

    async def save_span(self, span: SpanData) -> None:
        """Save a new span record.

        Args:
            span: Span data to persist
        """
        async with self.session_factory() as session:
            db_span = AITraceSpan(
                id=UUID(span["id"]),
                trace_id=UUID(span["trace_id"]),
                parent_span_id=UUID(span["parent_span_id"]) if span.get("parent_span_id") else None,
                session_id=UUID(span["session_id"]) if span.get("session_id") else None,
                span_type=span["span_type"],
                name=span["name"],
                provider=span.get("provider"),
                model=span.get("model"),
                model_version=span.get("model_version"),
                system_prompt=span.get("system_prompt"),
                user_prompt=span.get("user_prompt"),
                assistant_response=span.get("assistant_response"),
                input_data=span.get("input_data"),
                output_data=span.get("output_data"),
                started_at=_to_db_datetime(span["started_at"]),
                status=span["status"],
                input_tokens=span.get("input_tokens", 0),
                output_tokens=span.get("output_tokens", 0),
                error_message=span.get("error_message"),
                span_metadata=span.get("metadata", {}),
            )
            session.add(db_span)
            await session.commit()

            self.logger.debug("span_saved", span_id=span["id"], name=span["name"])

    async def save_reasoning(self, reasoning: ReasoningData) -> None:
        """Save a reasoning step record.

        Args:
            reasoning: Reasoning data to persist
        """
        async with self.session_factory() as session:
            db_reasoning = AITraceReasoning(
                id=UUID(reasoning["id"]),
                span_id=UUID(reasoning["span_id"]),
                step_number=reasoning["step_number"],
                step_type=reasoning["step_type"],
                description=reasoning["description"],
                input_context=reasoning.get("input_context"),
                output_result=reasoning.get("output_result"),
                confidence=reasoning.get("confidence"),
                dimension=reasoning.get("dimension"),
                raw_score=reasoning.get("raw_score"),
                weighted_score=reasoning.get("weighted_score"),
                weight_applied=reasoning.get("weight_applied"),
                explanation=reasoning.get("explanation"),
            )
            session.add(db_reasoning)
            await session.commit()

            self.logger.debug(
                "reasoning_saved",
                span_id=reasoning["span_id"],
                step=reasoning["step_number"],
            )

    async def update_trace(self, trace_id: UUID, updates: dict[str, Any]) -> None:
        """Update an existing trace.

        Args:
            trace_id: ID of the trace to update
            updates: Dictionary of fields to update
        """
        async with self.session_factory() as session:
            for field in ("started_at", "completed_at"):
                if field in updates and isinstance(updates[field], (str, datetime)):
                    updates[field] = _to_db_datetime(updates[field])

            stmt = update(AITrace).where(AITrace.id == trace_id).values(**updates)
            await session.execute(stmt)
            await session.commit()

            self.logger.debug("trace_updated", trace_id=str(trace_id), updates=list(updates.keys()))

    async def update_span(self, span_id: UUID, updates: dict[str, Any]) -> None:
        """Update an existing span.

        Args:
            span_id: ID of the span to update
            updates: Dictionary of fields to update
        """
        async with self.session_factory() as session:
            for field in ("started_at", "completed_at"):
                if field in updates and isinstance(updates[field], (str, datetime)):
                    updates[field] = _to_db_datetime(updates[field])

            stmt = update(AITraceSpan).where(AITraceSpan.id == span_id).values(**updates)
            await session.execute(stmt)
            await session.commit()

            self.logger.debug("span_updated", span_id=str(span_id), updates=list(updates.keys()))

    async def aggregate_trace_tokens(
        self, trace_id: UUID, input_tokens: int, output_tokens: int, cost: float
    ) -> None:
        """Add token usage to trace totals.

        Args:
            trace_id: ID of the trace
            input_tokens: Input tokens to add
            output_tokens: Output tokens to add
            cost: Cost to add
        """
        async with self.session_factory() as session:
            # Get current values
            result = await session.execute(
                select(AITrace.total_input_tokens, AITrace.total_output_tokens, AITrace.estimated_cost_usd)
                .where(AITrace.id == trace_id)
            )
            row = result.first()
            if row:
                new_input = (row.total_input_tokens or 0) + input_tokens
                new_output = (row.total_output_tokens or 0) + output_tokens
                new_cost = (row.estimated_cost_usd or 0.0) + cost

                stmt = (
                    update(AITrace)
                    .where(AITrace.id == trace_id)
                    .values(
                        total_input_tokens=new_input,
                        total_output_tokens=new_output,
                        estimated_cost_usd=new_cost,
                    )
                )
                await session.execute(stmt)
                await session.commit()

                self.logger.debug(
                    "trace_tokens_aggregated",
                    trace_id=str(trace_id),
                    total_input=new_input,
                    total_output=new_output,
                    total_cost=new_cost,
                )

    # Query methods for API/CLI

    async def get_trace(self, trace_id: UUID) -> Optional[AITrace]:
        """Get a trace by ID with all spans.

        Args:
            trace_id: ID of the trace

        Returns:
            AITrace with spans loaded, or None if not found
        """
        async with self.session_factory() as session:
            from sqlalchemy.orm import selectinload

            result = await session.execute(
                select(AITrace)
                .options(selectinload(AITrace.spans).selectinload(AITraceSpan.reasoning_steps))
                .where(AITrace.id == trace_id)
            )
            return result.scalar_one_or_none()

    async def list_traces(
        self,
        *,
        org_id: Optional[str] = None,
        trace_type: Optional[str] = None,
        status: Optional[str] = None,
        idea_id: Optional[int] = None,
        correlation_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AITrace]:
        """List traces with optional filters.

        Args:
            trace_type: Filter by trace type
            status: Filter by status
            idea_id: Filter by idea ID
            correlation_id: Filter by correlation ID
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            List of traces matching filters
        """
        async with self.session_factory() as session:
            from sqlalchemy.orm import selectinload

            query = select(AITrace).options(selectinload(AITrace.spans)).order_by(AITrace.started_at.desc())

            if org_id:
                query = query.where(AITrace.org_id == org_id)
            if trace_type:
                query = query.where(AITrace.trace_type == trace_type)
            if status:
                query = query.where(AITrace.status == status)
            if idea_id:
                query = query.where(AITrace.idea_id == idea_id)
            if correlation_id:
                query = query.where(AITrace.correlation_id == correlation_id)

            query = query.limit(limit).offset(offset)

            result = await session.execute(query)
            return list(result.scalars().all())

    async def get_traces_for_idea(
        self,
        idea_id: int,
        *,
        org_id: Optional[str] = None,
    ) -> list[AITrace]:
        """Get all traces for a specific idea.

        Args:
            idea_id: ID of the idea

        Returns:
            List of traces for the idea
        """
        return await self.list_traces(org_id=org_id, idea_id=idea_id, limit=100)

    async def get_trace_count(
        self,
        *,
        org_id: Optional[str] = None,
        trace_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> int:
        """Get count of traces matching filters.

        Args:
            trace_type: Filter by trace type
            status: Filter by status

        Returns:
            Count of matching traces
        """
        async with self.session_factory() as session:
            from sqlalchemy import func

            query = select(func.count(AITrace.id))

            if org_id:
                query = query.where(AITrace.org_id == org_id)
            if trace_type:
                query = query.where(AITrace.trace_type == trace_type)
            if status:
                query = query.where(AITrace.status == status)

            result = await session.execute(query)
            return result.scalar() or 0

    async def get_trace_metrics_summary(
        self,
        *,
        org_id: Optional[str] = None,
        from_ts: Optional[datetime] = None,
        to_ts: Optional[datetime] = None,
    ) -> dict[str, int | float]:
        """Get aggregate metrics for traces matching the optional filters."""
        async with self.session_factory() as session:
            query = select(
                func.count(AITrace.id).label("total_traces"),
                func.coalesce(
                    func.sum(case((AITrace.status == TraceStatus.COMPLETED, 1), else_=0)),
                    0,
                ).label("successful_traces"),
                func.coalesce(
                    func.sum(
                        case(
                            (AITrace.status.in_([TraceStatus.FAILED, TraceStatus.TIMEOUT]), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("failed_traces"),
                func.coalesce(func.sum(AITrace.total_input_tokens), 0).label("total_input_tokens"),
                func.coalesce(func.sum(AITrace.total_output_tokens), 0).label("total_output_tokens"),
                func.coalesce(func.sum(AITrace.estimated_cost_usd), 0.0).label("estimated_total_cost_usd"),
                func.coalesce(func.avg(AITrace.duration_ms), 0.0).label("avg_duration_ms"),
            )

            if org_id:
                query = query.where(AITrace.org_id == org_id)
            if from_ts:
                query = query.where(AITrace.started_at >= from_ts)
            if to_ts:
                query = query.where(AITrace.started_at <= to_ts)

            row = (await session.execute(query)).one()._mapping
            return {
                "total_traces": int(row["total_traces"] or 0),
                "successful_traces": int(row["successful_traces"] or 0),
                "failed_traces": int(row["failed_traces"] or 0),
                "total_input_tokens": int(row["total_input_tokens"] or 0),
                "total_output_tokens": int(row["total_output_tokens"] or 0),
                "estimated_total_cost_usd": float(row["estimated_total_cost_usd"] or 0.0),
                "avg_duration_ms": float(row["avg_duration_ms"] or 0.0),
            }
