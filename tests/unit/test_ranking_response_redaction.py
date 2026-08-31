"""Confidentiality regressions for provider ranking responses."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
import structlog
from structlog.testing import capture_logs

from src.services.ranking import RankingService

RESPONSE_MARKER = "CONFIDENTIAL-MALFORMED-PROVIDER-RESPONSE"
IDEA_NAME_MARKER = "CONFIDENTIAL-CUSTOMER-IDEA-NAME"


class _StopRanking(Exception):
    pass


class _RecordingTracer:
    def __init__(self, *, capture_prompts: bool) -> None:
        self.capture_prompts = capture_prompts
        self.trace_kwargs: dict[str, object] = {}

    @asynccontextmanager
    async def start_trace(self, *_args: object, **kwargs: object) -> AsyncIterator[None]:
        self.trace_kwargs = kwargs
        yield


def _ranking_service() -> RankingService:
    service = object.__new__(RankingService)
    service.logger = structlog.get_logger(__name__).bind(service="ranking")
    return service


def test_malformed_provider_response_is_not_copied_to_logs() -> None:
    service = _ranking_service()
    response = f"{RESPONSE_MARKER} {{not-json"

    with capture_logs() as logs:
        result = service._parse_json_response(response)

    captured = json.dumps(logs, sort_keys=True)
    assert result == {}
    assert RESPONSE_MARKER not in captured
    assert len(logs) == 1
    assert logs[0]["event"] == "json_parse_failed"
    assert logs[0]["error_code"] == "provider_response_invalid_json"
    assert logs[0]["exception_type"] == "JSONDecodeError"
    assert logs[0]["response_length"] == len(response)
    assert "text" not in logs[0]
    assert "exception" not in logs[0]


def test_valid_provider_response_parses_without_warning() -> None:
    service = _ranking_service()

    with capture_logs() as logs:
        result = service._parse_json_response('```json\n{"score": 97}\n```')

    assert result == {"score": 97}
    assert logs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_prompts", [False, True])
async def test_idea_name_requires_explicit_prompt_capture(capture_prompts: bool) -> None:
    tracer = _RecordingTracer(capture_prompts=capture_prompts)
    service = _ranking_service()
    service.tracer = tracer

    async def stop_after_trace_start(_idea: object) -> None:
        raise _StopRanking

    service._generate_swot = stop_after_trace_start  # type: ignore[method-assign]
    idea = SimpleNamespace(
        id=17,
        name=IDEA_NAME_MARKER,
        source_type=SimpleNamespace(value="manual"),
    )

    with capture_logs() as logs, pytest.raises(_StopRanking):
        await service.rank_idea(idea)  # type: ignore[arg-type]

    captured = json.dumps(
        {"logs": logs, "trace_kwargs": tracer.trace_kwargs},
        default=str,
        sort_keys=True,
    )
    assert (IDEA_NAME_MARKER in captured) is capture_prompts
    metadata = tracer.trace_kwargs["metadata"]
    assert isinstance(metadata, dict)
    assert ("idea_name" in metadata) is capture_prompts
