"""ASGI confidentiality contracts for unhandled request exceptions."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

import src.api.main as main_module
from src.api.main import request_observability_middleware
from src.tracing.tracer import Tracer
from src.tracing.types import ReasoningData, SpanData, TraceData, TraceType

DIRECT_EXCEPTION_MARKER = "SECRET-direct-ASGI-exception-do-not-retain"
TRACED_EXCEPTION_MARKER = "SECRET-traced-ASGI-exception-do-not-retain"
REQUEST_ERROR_CODE = "request_processing_failed"


class _RecordingStorage:
    """Capture every value the tracer attempts to persist."""

    def __init__(self) -> None:
        self.traces: list[TraceData] = []
        self.trace_updates: list[dict[str, Any]] = []

    async def save_trace(self, trace: TraceData) -> None:
        self.traces.append(deepcopy(trace))

    async def save_span(self, _span: SpanData) -> None:
        return None

    async def save_reasoning(self, _reasoning: ReasoningData) -> None:
        return None

    async def update_trace(self, trace_id: UUID, updates: dict[str, Any]) -> None:
        self.trace_updates.append({"trace_id": str(trace_id), **deepcopy(updates)})

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


def _test_app() -> tuple[FastAPI, dict[str, Any]]:
    test_app = FastAPI()
    test_app.middleware("http")(request_observability_middleware)
    metrics: dict[str, Any] = {
        "total_requests": 0,
        "in_flight": 0,
        "status_counts": defaultdict(int),
        "path_counts": defaultdict(int),
        "total_duration_ms": 0.0,
    }
    test_app.state.request_metrics = metrics
    test_app.state.rate_limiter = None
    return test_app, metrics


async def _get(app: FastAPI, path: str, request_id: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path, headers={"X-Request-ID": request_id})


def _assert_generic_500(
    response: httpx.Response,
    metrics: dict[str, Any],
    *,
    path: str,
    request_id: str,
) -> None:
    assert response.status_code == 500
    assert response.json() == {
        "detail": "Internal server error",
        "error_code": REQUEST_ERROR_CODE,
        "request_id": request_id,
    }
    assert response.headers["X-Request-ID"] == request_id
    assert float(response.headers["X-Response-Time-Ms"]) >= 0.0
    assert metrics["total_requests"] == 1
    assert metrics["in_flight"] == 0
    assert metrics["status_counts"] == {500: 1}
    assert metrics["path_counts"] == {path: 1}
    assert metrics["total_duration_ms"] >= 0.0


def _assert_redacted_request_log(logs: list[dict[str, Any]]) -> None:
    request_log = next(event for event in logs if event.get("event") == "request_failed")
    assert request_log["error_code"] == REQUEST_ERROR_CODE
    assert request_log["exception_type"] == "RuntimeError"
    assert "error" not in request_log
    assert "exception" not in request_log
    assert "exc_info" not in request_log


@pytest.mark.asyncio
async def test_direct_asgi_exception_is_consumed_without_confidential_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "trace_capture_prompts", False)
    test_app, metrics = _test_app()
    storage = _RecordingStorage()
    test_app.state.tracer = Tracer(storage=storage, capture_prompts=False)
    path = "/fail-direct"
    request_id = "request-direct-redaction"

    async def fail_direct() -> None:
        raise RuntimeError(DIRECT_EXCEPTION_MARKER)

    test_app.add_api_route(path, fail_direct, methods=["GET"])

    with capture_logs() as logs:
        response = await _get(test_app, path, request_id)

    _assert_generic_500(response, metrics, path=path, request_id=request_id)
    _assert_redacted_request_log(logs)
    assert DIRECT_EXCEPTION_MARKER not in response.text
    assert DIRECT_EXCEPTION_MARKER not in json.dumps(logs, default=str, sort_keys=True)
    assert DIRECT_EXCEPTION_MARKER not in json.dumps(storage.__dict__, default=str, sort_keys=True)
    assert storage.traces == []
    assert storage.trace_updates == []


@pytest.mark.asyncio
async def test_traced_asgi_exception_is_consumed_without_confidential_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main_module.settings, "trace_capture_prompts", False)
    test_app, metrics = _test_app()
    storage = _RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=False)
    path = "/fail-inside-trace"
    request_id = "request-traced-redaction"

    async def fail_inside_trace() -> None:
        async with tracer.start_trace(TraceType.RANKING):
            raise RuntimeError(TRACED_EXCEPTION_MARKER)

    test_app.add_api_route(path, fail_inside_trace, methods=["GET"])

    with capture_logs() as logs:
        response = await _get(test_app, path, request_id)

    _assert_generic_500(response, metrics, path=path, request_id=request_id)
    _assert_redacted_request_log(logs)
    persisted = json.dumps(storage.__dict__, default=str, sort_keys=True)
    captured_logs = json.dumps(logs, default=str, sort_keys=True)
    assert TRACED_EXCEPTION_MARKER not in response.text
    assert TRACED_EXCEPTION_MARKER not in captured_logs
    assert TRACED_EXCEPTION_MARKER not in persisted
    assert storage.trace_updates[-1]["error_message"] == "trace_execution_failed:RuntimeError"
    assert storage.trace_updates[-1]["error_type"] == "RuntimeError"
    assert storage.trace_updates[-1]["error_traceback"] is None
