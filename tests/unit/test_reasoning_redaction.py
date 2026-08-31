"""Confidentiality contracts for persisted reasoning records."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.tracing.context import TraceContext, reset_context, set_current_context
from src.tracing.tracer import REDACTED_REASONING_DESCRIPTION, Tracer
from src.tracing.types import ReasoningData, SpanData, SpanType, TraceData

SENSITIVE_REASONING_MARKER = "SECRET-model-reasoning-do-not-retain"


class _RecordingStorage:
    def __init__(self) -> None:
        self.reasoning: list[ReasoningData] = []

    async def save_trace(self, _trace: TraceData) -> None:
        return None

    async def save_span(self, _span: SpanData) -> None:
        return None

    async def save_reasoning(self, reasoning: ReasoningData) -> None:
        self.reasoning.append(deepcopy(reasoning))

    async def update_trace(self, _trace_id: UUID, _updates: dict[str, Any]) -> None:
        return None

    async def update_span(self, _span_id: UUID, _updates: dict[str, Any]) -> None:
        return None

    async def aggregate_trace_tokens(
        self,
        _trace_id: UUID,
        _input_tokens: int,
        _output_tokens: int,
        _cost: float,
    ) -> None:
        return None


async def _record_reasoning(*, capture_prompts: bool) -> ReasoningData:
    storage = _RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=capture_prompts)
    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        async with tracer.start_span(SpanType.LLM_CALL, "reasoning-confidentiality") as span:
            await span.record_reasoning(
                step_type="score_calculation",
                description=SENSITIVE_REASONING_MARKER,
                input_context={"prompt": SENSITIVE_REASONING_MARKER},
                output_result={"response": SENSITIVE_REASONING_MARKER},
                confidence=0.9,
                dimension="market",
                raw_score=80.0,
                weighted_score=20.0,
                weight_applied=0.25,
                explanation=SENSITIVE_REASONING_MARKER,
            )
            assert span._reasoning_steps == storage.reasoning
    finally:
        reset_context(token)

    assert len(storage.reasoning) == 1
    return storage.reasoning[0]


@pytest.mark.asyncio
async def test_capture_disabled_redacts_reasoning_content_before_persistence() -> None:
    reasoning = await _record_reasoning(capture_prompts=False)

    assert SENSITIVE_REASONING_MARKER not in json.dumps(reasoning, sort_keys=True)
    assert reasoning["description"] == REDACTED_REASONING_DESCRIPTION
    assert "input_context" not in reasoning
    assert "output_result" not in reasoning
    assert "explanation" not in reasoning
    assert reasoning["step_type"] == "score_calculation"
    assert reasoning["confidence"] == 0.9
    assert reasoning["dimension"] == "market"
    assert reasoning["raw_score"] == 80.0
    assert reasoning["weighted_score"] == 20.0
    assert reasoning["weight_applied"] == 0.25


@pytest.mark.asyncio
async def test_capture_enabled_retains_explicit_reasoning_content() -> None:
    reasoning = await _record_reasoning(capture_prompts=True)

    assert reasoning["description"] == SENSITIVE_REASONING_MARKER
    assert reasoning["input_context"] == {"prompt": SENSITIVE_REASONING_MARKER}
    assert reasoning["output_result"] == {"response": SENSITIVE_REASONING_MARKER}
    assert reasoning["explanation"] == SENSITIVE_REASONING_MARKER
