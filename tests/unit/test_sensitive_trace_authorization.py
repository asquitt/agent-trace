"""Authorization contracts for sensitive trace content."""

from __future__ import annotations

import inspect
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.api.routers.traces import export_trace_json, get_trace, get_trace_reasoning
from src.security import AuthContext
from src.tracing.storage.postgres import PostgresStorageBackend
from src.tracing.tracer import REDACTED_REASONING_DESCRIPTION

SENSITIVE_REASONING_MARKER = "SECRET-historical-reasoning-payload"
SENSITIVE_METADATA_MARKER = "SECRET-historical-trace-metadata"


class _TraceStorage:
    def __init__(self, trace: SimpleNamespace) -> None:
        self.trace = trace
        self.calls = 0
        self.materialized_calls = 0
        self.org_scopes: list[str | None] = []

    async def get_trace(
        self,
        _trace_id: UUID,
        *,
        org_id: str | None = None,
    ) -> SimpleNamespace | None:
        self.calls += 1
        self.org_scopes.append(org_id)
        if org_id is not None and self.trace.org_id != org_id:
            return None
        self.materialized_calls += 1
        return self.trace


class _NeverTraceStorage:
    async def get_trace(
        self,
        _trace_id: UUID,
        *,
        org_id: str | None = None,
    ) -> Any:
        raise AssertionError("trace storage must not be accessed by a non-admin prompt request")


class _EmptyResult:
    def scalar_one_or_none(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.statement: Any = None

    async def __aenter__(self) -> _RecordingSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, statement: Any) -> _EmptyResult:
        self.statement = statement
        return _EmptyResult()


class _SessionFactory:
    def __init__(self, session: _RecordingSession) -> None:
        self.session = session

    def __call__(self) -> _RecordingSession:
        return self.session


def _auth(
    *,
    roles: set[str],
    org_ids: set[str],
    requested_org_id: str | None = "acme",
) -> AuthContext:
    return AuthContext(
        subject="trace-test",
        roles=frozenset(roles),
        org_ids=frozenset(org_ids),
        auth_enabled=True,
        requested_org_id=requested_org_id,
    )


def _trace(*, org_id: str | None = "acme") -> SimpleNamespace:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    reasoning = SimpleNamespace(
        id=uuid4(),
        step_number=1,
        step_type="score_calculation",
        description=SENSITIVE_REASONING_MARKER,
        dimension="market",
        raw_score=80.0,
        weighted_score=20.0,
        weight_applied=0.25,
        explanation=SENSITIVE_REASONING_MARKER,
        confidence=0.9,
    )
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
        error_message="span-sensitive-error",
        system_prompt="system-secret",
        user_prompt="user-secret",
        assistant_response="assistant-secret",
        reasoning_steps=[reasoning],
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
        error_message="trace-sensitive-error",
        tags=[],
        trace_metadata={"customer_context": SENSITIVE_METADATA_MARKER},
        spans=[span],
    )


def test_trace_prompt_defaults_are_redacted() -> None:
    detail_default = inspect.signature(get_trace).parameters["include_prompts"].default
    export_default = inspect.signature(export_trace_json).parameters["include_prompts"].default
    reasoning_default = inspect.signature(get_trace_reasoning).parameters[
        "include_prompts"
    ].default

    assert getattr(detail_default, "default", None) is False
    assert getattr(export_default, "default", None) is False
    assert getattr(reasoning_default, "default", None) is False


@pytest.mark.asyncio
async def test_postgres_trace_lookup_applies_org_filter_before_eager_load() -> None:
    session = _RecordingSession()
    storage = PostgresStorageBackend(_SessionFactory(session))  # type: ignore[arg-type]

    await storage.get_trace(uuid4(), org_id="acme")

    assert session.statement is not None
    where_clause = session.statement.whereclause
    assert where_clause is not None
    compiled_where = where_clause.compile()
    assert "ai_traces.org_id" in str(compiled_where)
    assert "acme" in compiled_where.params.values()


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
async def test_tenant_viewer_cannot_request_sensitive_reasoning_material() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_trace_reasoning(
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
    assert storage.materialized_calls == 1
    assert storage.org_scopes == ["acme"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_foreign_trace_is_not_materialized_or_identified(endpoint: Any) -> None:
    trace = _trace(org_id="contoso")
    storage = _TraceStorage(trace)

    with pytest.raises(HTTPException) as exc:
        await endpoint(
            trace.id,
            storage,  # type: ignore[arg-type]
            _auth(roles={"admin"}, org_ids={"acme"}),
            True,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Trace not found"
    assert storage.calls == 1
    assert storage.materialized_calls == 0
    assert storage.org_scopes == ["acme"]


@pytest.mark.asyncio
async def test_foreign_reasoning_trace_is_not_materialized_or_identified() -> None:
    trace = _trace(org_id="contoso")
    storage = _TraceStorage(trace)

    with pytest.raises(HTTPException) as exc:
        await get_trace_reasoning(
            trace.id,
            storage,  # type: ignore[arg-type]
            _auth(roles={"viewer"}, org_ids={"acme"}),
            False,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Trace not found"
    assert storage.materialized_calls == 0
    assert storage.org_scopes == ["acme"]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_default_view_hides_historical_sensitive_payloads(endpoint: Any) -> None:
    trace = _trace()
    result = await endpoint(
        trace.id,
        _TraceStorage(trace),  # type: ignore[arg-type]
        _auth(roles={"viewer"}, org_ids={"acme"}),
        False,
    )

    if hasattr(result, "spans"):
        trace_error = result.error_message
        span_error = result.spans[0].error_message
        metadata = result.metadata
    else:
        trace_error = result["trace"]["error_message"]
        span_error = result["spans"][0]["error_message"]
        metadata = result["trace"]["metadata"]

    assert trace_error is None
    assert span_error is None
    assert metadata == {}
    serialized = (
        json.dumps(result.model_dump(mode="json"), sort_keys=True)
        if hasattr(result, "model_dump")
        else json.dumps(result, sort_keys=True)
    )
    assert SENSITIVE_REASONING_MARKER not in serialized
    assert SENSITIVE_METADATA_MARKER not in serialized
    assert REDACTED_REASONING_DESCRIPTION in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [get_trace, export_trace_json])
async def test_explicit_admin_sensitive_view_can_read_sensitive_payloads(endpoint: Any) -> None:
    trace = _trace()
    result = await endpoint(
        trace.id,
        _TraceStorage(trace),  # type: ignore[arg-type]
        _auth(roles={"admin"}, org_ids={"acme"}),
        True,
    )

    if hasattr(result, "spans"):
        trace_error = result.error_message
        span_error = result.spans[0].error_message
        metadata = result.metadata
    else:
        trace_error = result["trace"]["error_message"]
        span_error = result["spans"][0]["error_message"]
        metadata = result["trace"]["metadata"]

    assert trace_error == "trace-sensitive-error"
    assert span_error == "span-sensitive-error"
    assert metadata == {"customer_context": SENSITIVE_METADATA_MARKER}
    serialized = (
        json.dumps(result.model_dump(mode="json"), sort_keys=True)
        if hasattr(result, "model_dump")
        else json.dumps(result, sort_keys=True)
    )
    assert SENSITIVE_REASONING_MARKER in serialized
    assert SENSITIVE_METADATA_MARKER in serialized


@pytest.mark.asyncio
async def test_default_reasoning_view_redacts_model_derived_text() -> None:
    trace = _trace()

    reasoning = await get_trace_reasoning(
        trace.id,
        _TraceStorage(trace),  # type: ignore[arg-type]
        _auth(roles={"viewer"}, org_ids={"acme"}),
        False,
    )

    assert len(reasoning) == 1
    assert reasoning[0].description == REDACTED_REASONING_DESCRIPTION
    assert reasoning[0].explanation is None
    assert reasoning[0].dimension == "market"
    assert reasoning[0].raw_score == 80.0
    assert SENSITIVE_REASONING_MARKER not in json.dumps(
        [item.model_dump(mode="json") for item in reasoning],
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_explicit_admin_reasoning_view_can_read_model_derived_text() -> None:
    trace = _trace()

    reasoning = await get_trace_reasoning(
        trace.id,
        _TraceStorage(trace),  # type: ignore[arg-type]
        _auth(roles={"admin"}, org_ids={"acme"}),
        True,
    )

    assert reasoning[0].description == SENSITIVE_REASONING_MARKER
    assert reasoning[0].explanation == SENSITIVE_REASONING_MARKER


@pytest.mark.asyncio
async def test_global_admin_may_use_unscoped_lookup() -> None:
    trace = _trace(org_id=None)
    storage = _TraceStorage(trace)

    result = await get_trace(
        trace.id,
        storage,  # type: ignore[arg-type]
        _auth(roles={"admin"}, org_ids={"*"}, requested_org_id=None),
        False,
    )

    assert result.id == str(trace.id)
    assert storage.org_scopes == [None]
