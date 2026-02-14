"""Anthropic client wrapper with automatic tracing.

This module provides a traced wrapper around the Anthropic client that
automatically creates spans for each API call with full prompt/response capture.
"""

from typing import Any, Optional, cast

import anthropic
from anthropic import AsyncAnthropic

from ..context import get_current_context
from ..tracer import Tracer
from ..types import SpanType


class TracedAnthropicClient:
    """Anthropic client wrapper with automatic tracing.

    Wraps the AsyncAnthropic client to automatically create spans for each
    API call, capturing prompts, responses, and token usage.

    Example:
        client = TracedAnthropicClient(tracer, api_key="sk-...")

        async with tracer.start_trace(TraceType.RANKING, idea_id=123):
            response = await client.create_message(
                model="claude-sonnet-4-20250514",
                max_tokens=2000,
                system="You are an expert analyst...",
                messages=[{"role": "user", "content": "Analyze this idea..."}],
                span_name="swot_analysis",
            )
    """

    # Claude model pricing (per 1M tokens as of 2025)
    PRICING = {
        "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
        "claude-haiku-3-5-20241022": {"input": 0.80, "output": 4.00},
        "claude-opus-4-20250514": {"input": 15.00, "output": 75.00},
        # Fallback for unknown models
        "default": {"input": 3.00, "output": 15.00},
    }

    def __init__(
        self,
        tracer: Tracer,
        client: Optional[AsyncAnthropic] = None,
        api_key: Optional[str] = None,
    ):
        """Initialize the traced Anthropic client.

        Args:
            tracer: The Tracer instance to use for tracing
            client: Optional existing AsyncAnthropic client
            api_key: Optional API key (used if client not provided)
        """
        self.tracer = tracer
        self.client = client or AsyncAnthropic(api_key=api_key)

    async def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Optional[str] = None,
        messages: list[dict[str, Any]],
        span_name: str = "anthropic_message",
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> anthropic.types.Message:
        """Create a message with automatic tracing.

        This method wraps the Anthropic messages.create API with automatic
        span creation, prompt capture, and token tracking.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-20250514")
            max_tokens: Maximum tokens to generate
            system: Optional system prompt
            messages: List of message dicts with "role" and "content"
            span_name: Name for the tracing span
            temperature: Optional temperature setting
            top_p: Optional top_p setting
            top_k: Optional top_k setting
            metadata: Optional metadata for the span
            **kwargs: Additional arguments passed to the API

        Returns:
            Anthropic Message response

        Raises:
            RuntimeError: If no active trace context exists
        """
        ctx = get_current_context()
        if not ctx:
            # No trace context - just make the call without tracing
            return await self._raw_create_message(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                **kwargs,
            )

        # Extract user prompt from messages
        user_prompt = self._extract_user_prompt(messages)

        # Build input data for tracing
        input_data: dict[str, int | float] = {
            "max_tokens": max_tokens,
            "message_count": len(messages),
        }
        if temperature is not None:
            input_data["temperature"] = temperature
        if top_p is not None:
            input_data["top_p"] = top_p
        if top_k is not None:
            input_data["top_k"] = top_k

        async with self.tracer.start_span(
            SpanType.LLM_CALL,
            span_name,
            provider="anthropic",
            model=model,
            system_prompt=system,
            user_prompt=user_prompt,
            input_data=input_data,
            metadata=metadata,
        ) as span:
            # Make the actual API call
            response = await self._raw_create_message(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                **kwargs,
            )

            # Extract response text
            response_text = self._extract_response_text(response)

            # Record in span
            span.record_response(response_text)
            span.record_tokens(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            )
            span.record_output(
                {
                    "id": response.id,
                    "model": response.model,
                    "stop_reason": response.stop_reason,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                    },
                }
            )

            return response

    async def _raw_create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: Optional[str],
        messages: list[dict[str, Any]],
        temperature: Optional[float],
        top_p: Optional[float],
        top_k: Optional[int],
        **kwargs: Any,
    ) -> anthropic.types.Message:
        """Make the raw API call without tracing."""
        request_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            request_kwargs["system"] = system
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if top_p is not None:
            request_kwargs["top_p"] = top_p
        if top_k is not None:
            request_kwargs["top_k"] = top_k
        request_kwargs.update(kwargs)

        create = cast(Any, self.client.messages.create)
        return await create(**request_kwargs)

    def _extract_user_prompt(self, messages: list[dict[str, Any]]) -> str:
        """Extract user prompt from messages for tracing."""
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return ""

        # Get the last user message
        last_user = user_messages[-1]
        content = last_user.get("content", "")

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Handle content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif isinstance(block, str):
                    text_parts.append(block)
            return "\n".join(text_parts)

        return str(content)

    def _extract_response_text(self, response: anthropic.types.Message) -> str:
        """Extract text content from response."""
        text_parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                block_text = getattr(block, "text", None)
                if isinstance(block_text, str):
                    text_parts.append(block_text)
        return "\n".join(text_parts)

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate estimated cost in USD.

        Args:
            model: Model identifier
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Estimated cost in USD
        """
        pricing = self.PRICING.get(model, self.PRICING["default"])
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost
