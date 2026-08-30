"""Authorization contracts for sensitive trace content."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.api.routers.traces import export_trace_json, get_trace
from src.security import AuthContext


class _TraceStorage:
    def __init__(self, trace: SimpleNamespace) -> None:
        self.trace = trace
        self.calls = 0

    async def get_trace(self, _trace_id: UUID) -> SimpleNamespace:
        self.calls += 1
        return self.trace


class _NeverTraceStorage:
    async def get_trace(self, _trace_id: UUID) -> Any:
        raise AssertionError("trace storage must not be accessed by a non-admin prompt request")


def _auth(*, roles: set[str], org_ids: set[str]) -> AuthContext:
    return AuthContext(
        subject="trace-test",
        roles=frozenset(roles),
        org_ids=frozenset(org_ids),
        auth_enabled=True,
        requested_org_id="acme",
    )


def _trace(*, org_id: str = "acme") -> SimpleNamespace:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    span = SimpleNamespace(
        id=uuid4(),
        span_type="llm_call",
        name="sensitive-call",
        provider="test-provider",
        model="test-model",
        started_at=now,
        completed_at=now,
        duration_ms=1,
        input_tokens=2,
        output_tokens=3,
        status="completed",
        error_message=None,
        system_prompt="system-secret",
        user_prompt="user-secret",
        assistant_response="assistant-secret",
        reasoning_steps=[],
    )
    return SimpleNamespace(
        id=uuid4(),
        correlation_id=uuid4(),
        trace_type="ranking",
        status="completed",
        org_id=org_id,
        deployment_id=None,
        session_id=None,
        agent_id="agent-1",
        idea_id=None,
        ranking_id=None,
        started_at=now,
        completed_at=now,
        duration_ms=1,
        total_input_tokens=2,
        total_output_tokens=3,
        estimated_cost_usd=0.01,
        error_message=None,
        tags=[],
        trace_metadata={},
        spans=[span],
    )


def test_trace_prompt_defaults_are_redacted() -> None:
    detail_default = inspect.signature(get_trace).parameters["include_prompts"].default
    export_default = inspect.signature(export_trace_json).parameters["include_prompts"].default

    assert getattr(detail_default, "default", None) is False
    assert getattr(export_default, "default", None) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_tenant_viewer_cannot_request_prompt_material(endpoint: Any) -> None:
    with pytest.raises(HTTPException) as exc:
        await endpoint(
            uuid4(),
            _NeverTraceStorage(),  # type: ignore[arg-type]
            _auth(roles={"viewer"}, org_ids={"acme"}),
            True,
        )

    assert exc.value.status_code == 403
    assert "admin" in str(exc.value.detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_tenant_admin_can_request_own_prompt_material(endpoint: Any) -> None:
    trace = _trace()
    storage = _TraceStorage(trace)

    result = await endpoint(
        trace.id,
        storage,  # type: ignore[arg-type]
        _auth(roles={"admin"}, org_ids={"acme"}),
        True,
    )

    spans = result.spans if hasattr(result, "spans") else result["spans"]
    first_span = spans[0]
    system_prompt = (
        first_span.system_prompt
        if hasattr(first_span, "system_prompt")
        else first_span["system_prompt"]
    )
    assert system_prompt == "system-secret"
    assert storage.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_tenant_admin_still_requires_trace_org_access(endpoint: Any) -> None:
    trace = _trace(org_id="contoso")

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            trace.id,
            _TraceStorage(trace),  # type: ignore[arg-type]
            _auth(roles={"admin"}, org_ids={"acme"}),
            True,
        )

    assert exc.value.status_code == 403
    assert "Request org mismatch" in str(exc.value.detail)
