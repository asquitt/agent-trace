"""Unit tests for tracing decorators."""

from contextlib import asynccontextmanager
from typing import Any

import pytest

from src.tracing.decorators import traced
from src.tracing.types import TraceType


class _SpyTracer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @asynccontextmanager
    async def start_trace(self, trace_type: TraceType, **kwargs: Any):
        self.calls.append({"trace_type": trace_type, "kwargs": kwargs})
        yield


@pytest.mark.asyncio
async def test_traced_name_populates_operation_metadata_and_tag() -> None:
    tracer = _SpyTracer()

    @traced(TraceType.RANKING, name="rank_pipeline", tags=["pipeline"])
    async def run() -> str:
        return "ok"

    result = await run(_tracer=tracer)

    assert result == "ok"
    assert len(tracer.calls) == 1
    call = tracer.calls[0]
    assert call["kwargs"]["metadata"]["operation_name"] == "rank_pipeline"
    assert "pipeline" in call["kwargs"]["tags"]
    assert "operation:rank_pipeline" in call["kwargs"]["tags"]


@pytest.mark.asyncio
async def test_traced_defaults_operation_name_to_function_name() -> None:
    tracer = _SpyTracer()

    @traced(TraceType.RANKING)
    async def default_name() -> None:
        return None

    await default_name(_tracer=tracer)

    call = tracer.calls[0]
    assert call["kwargs"]["metadata"]["operation_name"] == "default_name"
