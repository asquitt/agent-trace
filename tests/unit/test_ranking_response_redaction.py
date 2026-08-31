"""Confidentiality regressions for provider ranking responses."""

import json

import structlog
from structlog.testing import capture_logs

from src.services.ranking import RankingService

RESPONSE_MARKER = "CONFIDENTIAL-MALFORMED-PROVIDER-RESPONSE"


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
