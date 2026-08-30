"""Fail-closed contracts for traced AI provider execution and prompt capture."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

import src.dependencies as dependencies_module
import src.tracing.providers.anthropic as anthropic_provider_module
import src.tracing.providers.openai as openai_provider_module
from src.config import Settings
from src.tracing.context import TraceContext, reset_context, set_current_context
from src.tracing.providers.anthropic import TracedAnthropicClient
from src.tracing.providers.governance import ProviderExecutionDisabledError
from src.tracing.providers.openai import TracedOpenAIClient
from src.tracing.tracer import Tracer
from src.tracing.types import ReasoningData, SpanData, SpanType, TraceData, TraceType

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_PROMPT = "SECRET-customer-prompt-do-not-retain"
SECRET_EXCEPTION_MARKER = "SECRET-provider-error-payload-do-not-retain"
INVALID_BOOLEAN_VALUES = ["false", "true", 0, 1, object()]
INVALID_BOOLEAN_IDS = ["false-string", "true-string", "zero", "one", "object"]


class RecordingStorage:
    """In-memory storage that records exactly what the tracer persists."""

    def __init__(self) -> None:
        self.traces: list[TraceData] = []
        self.spans: list[SpanData] = []
        self.reasoning: list[ReasoningData] = []
        self.trace_updates: list[dict[str, Any]] = []
        self.span_updates: list[dict[str, Any]] = []

    async def save_trace(self, trace: TraceData) -> None:
        self.traces.append(deepcopy(trace))

    async def save_span(self, span: SpanData) -> None:
        self.spans.append(deepcopy(span))

    async def save_reasoning(self, reasoning: ReasoningData) -> None:
        self.reasoning.append(deepcopy(reasoning))

    async def update_trace(self, trace_id: UUID, updates: dict[str, Any]) -> None:
        self.trace_updates.append({"trace_id": str(trace_id), **deepcopy(updates)})

    async def update_span(self, span_id: UUID, updates: dict[str, Any]) -> None:
        self.span_updates.append({"span_id": str(span_id), **deepcopy(updates)})

    async def aggregate_trace_tokens(
        self,
        trace_id: UUID,
        input_tokens: int,
        output_tokens: int,
        cost: float,
    ) -> None:
        self.trace_updates.append(
            {
                "trace_id": str(trace_id),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
            }
        )


class RevokingStorage(RecordingStorage):
    """Storage hook that can revoke provider access while saving a span."""

    def __init__(self) -> None:
        super().__init__()
        self.on_save_span: Callable[[], None] | None = None

    async def save_span(self, span: SpanData) -> None:
        await super().save_span(span)
        if self.on_save_span is not None:
            self.on_save_span()


class FakeEmbeddings:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        inputs = kwargs["input"]
        count = len(inputs) if isinstance(inputs, list) else 1
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[float(index), 0.5])
                for index in range(count)
            ],
            usage=SimpleNamespace(prompt_tokens=3, total_tokens=3),
            model=kwargs["model"],
        )


class FakeOpenAIClient:
    def __init__(self) -> None:
        self.embeddings = FakeEmbeddings()


class FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="message-1",
            model=kwargs["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=4, output_tokens=2),
            content=[SimpleNamespace(type="text", text="safe response")],
        )


class FakeAnthropicClient:
    def __init__(self) -> None:
        self.messages = FakeMessages()


def test_provider_execution_defaults_disabled_and_env_example_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PROVIDER_EXECUTION_ENABLED", raising=False)

    assert Settings(_env_file=None).provider_execution_enabled is False

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "PROVIDER_EXECUTION_ENABLED=false" in env_example
    assert Settings(_env_file=PROJECT_ROOT / ".env.example").provider_execution_enabled is False

    monkeypatch.setenv("PROVIDER_EXECUTION_ENABLED", "true")
    assert Settings(_env_file=None).provider_execution_enabled is True


@pytest.mark.parametrize(
    ("dependency_name", "wrapper_name"),
    [
        ("get_openai_client", "TracedOpenAIClient"),
        ("get_anthropic_client", "TracedAnthropicClient"),
    ],
)
def test_dependency_injection_passes_provider_execution_setting(
    monkeypatch: pytest.MonkeyPatch,
    dependency_name: str,
    wrapper_name: str,
) -> None:
    captured: dict[str, Any] = {}

    class WrapperProbe:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    settings = Settings(_env_file=None, provider_execution_enabled=True)
    tracer = object()
    monkeypatch.setattr(dependencies_module, "get_settings", lambda: settings)
    monkeypatch.setattr(dependencies_module, "get_tracer", lambda: tracer)
    monkeypatch.setattr(dependencies_module, wrapper_name, WrapperProbe)

    getattr(dependencies_module, dependency_name)()

    assert captured["tracer"] is tracer
    assert captured["execution_enabled"] is True


@pytest.mark.parametrize("invalid_value", INVALID_BOOLEAN_VALUES, ids=INVALID_BOOLEAN_IDS)
def test_tracer_rejects_non_boolean_capture_setting(invalid_value: object) -> None:
    with pytest.raises(TypeError, match="capture_prompts must be a boolean"):
        Tracer(storage=RecordingStorage(), capture_prompts=invalid_value)  # type: ignore[arg-type]


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
@pytest.mark.parametrize("invalid_value", INVALID_BOOLEAN_VALUES, ids=INVALID_BOOLEAN_IDS)
def test_provider_constructors_reject_non_boolean_execution_setting(
    provider_name: str,
    invalid_value: object,
) -> None:
    tracer = Tracer(storage=RecordingStorage())

    with pytest.raises(TypeError, match="execution_enabled must be a boolean"):
        if provider_name == "openai":
            TracedOpenAIClient(
                tracer=tracer,
                client=FakeOpenAIClient(),
                execution_enabled=invalid_value,  # type: ignore[arg-type]
            )
        else:
            TracedAnthropicClient(
                tracer=tracer,
                client=FakeAnthropicClient(),
                execution_enabled=invalid_value,  # type: ignore[arg-type]
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
@pytest.mark.parametrize("invalid_value", INVALID_BOOLEAN_VALUES, ids=INVALID_BOOLEAN_IDS)
async def test_mutated_execution_setting_never_authorizes_provider_calls(
    provider_name: str,
    invalid_value: object,
) -> None:
    tracer = Tracer(storage=RecordingStorage())

    if provider_name == "openai":
        injected_client = FakeOpenAIClient()
        provider = TracedOpenAIClient(
            tracer=tracer,
            client=injected_client,
            execution_enabled=True,
        )
        provider.execution_enabled = invalid_value  # type: ignore[assignment]
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.create_embedding("blocked")
        assert injected_client.embeddings.calls == []
    else:
        injected_client = FakeAnthropicClient()
        provider = TracedAnthropicClient(
            tracer=tracer,
            client=injected_client,
            execution_enabled=True,
        )
        provider.execution_enabled = invalid_value  # type: ignore[assignment]
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.create_message(
                model="claude-sonnet-4-20250514",
                max_tokens=10,
                messages=[{"role": "user", "content": "blocked"}],
            )
        assert injected_client.messages.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value", INVALID_BOOLEAN_VALUES, ids=INVALID_BOOLEAN_IDS)
async def test_mutated_capture_setting_never_enables_openai_preview(
    invalid_value: object,
) -> None:
    storage = RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=False)
    tracer.capture_prompts = invalid_value  # type: ignore[assignment]
    provider = TracedOpenAIClient(
        tracer=tracer,
        client=FakeOpenAIClient(),
        execution_enabled=True,
    )

    async with tracer.start_trace(TraceType.EMBEDDING):
        await provider.create_embedding(SECRET_PROMPT)

    assert SECRET_PROMPT not in json.dumps(storage.spans, sort_keys=True)
    assert "text_preview" not in storage.spans[0]["input_data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_value", INVALID_BOOLEAN_VALUES, ids=INVALID_BOOLEAN_IDS)
async def test_mutated_capture_setting_never_persists_tracer_prompts_or_response(
    invalid_value: object,
) -> None:
    storage = RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=False)
    tracer.capture_prompts = invalid_value  # type: ignore[assignment]
    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        async with tracer.start_span(
            SpanType.LLM_CALL,
            "mutated_capture",
            system_prompt=SECRET_PROMPT,
            user_prompt=SECRET_PROMPT,
        ) as span:
            span.record_response(SECRET_PROMPT)
    finally:
        reset_context(token)

    persisted = json.dumps(
        {"spans": storage.spans, "span_updates": storage.span_updates},
        sort_keys=True,
    )
    assert SECRET_PROMPT not in persisted


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_scope", ["trace", "span"])
async def test_capture_disabled_redacts_exception_payloads_from_storage_and_logs(
    failure_scope: str,
) -> None:
    storage = RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=False)

    with capture_logs() as logs:
        with pytest.raises(RuntimeError, match=SECRET_EXCEPTION_MARKER):
            if failure_scope == "trace":
                async with tracer.start_trace(TraceType.RANKING):
                    raise RuntimeError(SECRET_EXCEPTION_MARKER)
            else:
                token = set_current_context(
                    TraceContext(trace_id=uuid4(), correlation_id=uuid4())
                )
                try:
                    async with tracer.start_span(SpanType.LLM_CALL, "provider_failure"):
                        raise RuntimeError(SECRET_EXCEPTION_MARKER)
                finally:
                    reset_context(token)

    persisted = json.dumps(
        {
            "trace_updates": storage.trace_updates,
            "span_updates": storage.span_updates,
        },
        sort_keys=True,
    )
    captured_logs = json.dumps(logs, sort_keys=True)

    assert SECRET_EXCEPTION_MARKER not in persisted
    assert SECRET_EXCEPTION_MARKER not in captured_logs
    assert "Traceback (most recent call last)" not in persisted
    assert "Traceback (most recent call last)" not in captured_logs

    expected_code = f"{failure_scope}_execution_failed:RuntimeError"
    updates = storage.trace_updates if failure_scope == "trace" else storage.span_updates
    assert updates[-1]["error_message"] == expected_code
    if failure_scope == "trace":
        assert updates[-1]["error_type"] == "RuntimeError"
        assert updates[-1]["error_traceback"] is None
    assert logs[-1]["error"] == expected_code
    assert logs[-1]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_openai_revocation_during_save_span_blocks_provider_call(batch: bool) -> None:
    storage = RevokingStorage()
    tracer = Tracer(storage=storage)
    injected_client = FakeOpenAIClient()
    provider = TracedOpenAIClient(
        tracer=tracer,
        client=injected_client,
        execution_enabled=True,
    )
    storage.on_save_span = lambda: setattr(provider, "execution_enabled", False)

    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        with pytest.raises(ProviderExecutionDisabledError):
            if batch:
                await provider.create_embeddings_batch(["blocked"])
            else:
                await provider.create_embedding("blocked")
    finally:
        reset_context(token)

    assert len(storage.spans) == 1
    assert provider.execution_enabled is False
    assert injected_client.embeddings.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_openai_rechecks_immediately_before_provider_call(
    batch: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = RecordingStorage()
    tracer = Tracer(storage=storage)
    injected_client = FakeOpenAIClient()
    provider = TracedOpenAIClient(
        tracer=tracer,
        client=injected_client,
        execution_enabled=True,
    )
    original_require_client = provider._require_client
    guard_calls = 0

    def revoke_on_final_guard() -> Any:
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 2:
            provider.execution_enabled = False
        return original_require_client()

    monkeypatch.setattr(provider, "_require_client", revoke_on_final_guard)
    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        with pytest.raises(ProviderExecutionDisabledError):
            if batch:
                await provider.create_embeddings_batch(["blocked"])
            else:
                await provider.create_embedding("blocked")
    finally:
        reset_context(token)

    assert guard_calls == 2
    assert injected_client.embeddings.calls == []


@pytest.mark.asyncio
async def test_openai_disabled_blocks_construction_and_every_request_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls = 0

    def constructor_probe(*args: Any, **kwargs: Any) -> Any:
        nonlocal constructor_calls
        constructor_calls += 1
        raise AssertionError("OpenAI SDK client must not be constructed")

    monkeypatch.setattr(openai_provider_module, "AsyncOpenAI", constructor_probe)
    storage = RecordingStorage()
    tracer = Tracer(storage=storage)

    without_injected_client = TracedOpenAIClient(tracer=tracer, api_key="unused")
    assert constructor_calls == 0

    injected_client = FakeOpenAIClient()
    provider = TracedOpenAIClient(tracer=tracer, client=injected_client)
    assert provider.client is None

    with pytest.raises(ProviderExecutionDisabledError):
        await provider.create_embedding("no-context")
    with pytest.raises(ProviderExecutionDisabledError):
        await provider._raw_create_embedding("raw", "text-embedding-3-small", None)
    with pytest.raises(ProviderExecutionDisabledError):
        await provider._raw_create_embeddings_batch(["raw-batch"], "text-embedding-3-small", None)

    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.create_embedding("traced")
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.create_embeddings_batch(["traced-batch"])
    finally:
        reset_context(token)

    assert injected_client.embeddings.calls == []
    assert storage.traces == []
    assert storage.spans == []
    assert without_injected_client.client is None


@pytest.mark.asyncio
async def test_anthropic_disabled_blocks_construction_and_every_request_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_calls = 0

    def constructor_probe(*args: Any, **kwargs: Any) -> Any:
        nonlocal constructor_calls
        constructor_calls += 1
        raise AssertionError("Anthropic SDK client must not be constructed")

    monkeypatch.setattr(anthropic_provider_module, "AsyncAnthropic", constructor_probe)
    storage = RecordingStorage()
    tracer = Tracer(storage=storage)

    without_injected_client = TracedAnthropicClient(tracer=tracer, api_key="unused")
    assert constructor_calls == 0

    injected_client = FakeAnthropicClient()
    provider = TracedAnthropicClient(tracer=tracer, client=injected_client)
    assert provider.client is None
    request = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 10,
        "system": None,
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": None,
        "top_p": None,
        "top_k": None,
    }

    with pytest.raises(ProviderExecutionDisabledError):
        await provider.create_message(
            model=request["model"],
            max_tokens=request["max_tokens"],
            system=request["system"],
            messages=request["messages"],
        )
    with pytest.raises(ProviderExecutionDisabledError):
        await provider._raw_create_message(**request)

    token = set_current_context(TraceContext(trace_id=uuid4(), correlation_id=uuid4()))
    try:
        with pytest.raises(ProviderExecutionDisabledError):
            await provider.create_message(
                model=request["model"],
                max_tokens=request["max_tokens"],
                system=request["system"],
                messages=request["messages"],
            )
    finally:
        reset_context(token)

    assert injected_client.messages.calls == []
    assert storage.traces == []
    assert storage.spans == []
    assert without_injected_client.client is None


@pytest.mark.asyncio
@pytest.mark.parametrize("capture_prompts", [False, True])
async def test_openai_prompt_preview_requires_explicit_capture(capture_prompts: bool) -> None:
    storage = RecordingStorage()
    tracer = Tracer(storage=storage, capture_prompts=capture_prompts)
    injected_client = FakeOpenAIClient()
    provider = TracedOpenAIClient(
        tracer=tracer,
        client=injected_client,
        execution_enabled=True,
    )

    async with tracer.start_trace(TraceType.EMBEDDING):
        result = await provider.create_embedding(SECRET_PROMPT)

    assert result == [0.0, 0.5]
    assert len(injected_client.embeddings.calls) == 1
    input_data = storage.spans[0]["input_data"]
    assert input_data is not None
    assert input_data["text_length"] == len(SECRET_PROMPT)

    persisted = json.dumps(
        {
            "traces": storage.traces,
            "spans": storage.spans,
            "trace_updates": storage.trace_updates,
            "span_updates": storage.span_updates,
        },
        sort_keys=True,
    )
    if capture_prompts:
        assert input_data["text_preview"] == SECRET_PROMPT
        assert SECRET_PROMPT in persisted
    else:
        assert "text_preview" not in input_data
        assert SECRET_PROMPT not in persisted


@pytest.mark.asyncio
async def test_enabled_injected_anthropic_client_can_execute_without_context() -> None:
    injected_client = FakeAnthropicClient()
    provider = TracedAnthropicClient(
        tracer=Tracer(storage=RecordingStorage()),
        client=injected_client,
        execution_enabled=True,
    )

    response = await provider.create_message(
        model="claude-sonnet-4-20250514",
        max_tokens=10,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert response.id == "message-1"
    assert len(injected_client.messages.calls) == 1
