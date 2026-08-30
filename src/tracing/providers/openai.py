"""OpenAI client wrapper with automatic tracing.

This module provides a traced wrapper around the OpenAI client that
automatically creates spans for embedding API calls.
"""

from typing import Optional

from openai import AsyncOpenAI

from ..context import get_current_context
from ..tracer import Tracer
from ..types import SpanType
from .governance import require_provider_execution, validate_execution_enabled


class TracedOpenAIClient:
    """OpenAI client wrapper with automatic tracing.

    Wraps the AsyncOpenAI client to automatically create spans for each
    API call, capturing inputs and token usage.

    Example:
        client = TracedOpenAIClient(
            tracer,
            api_key="sk-...",
            execution_enabled=True,
        )

        async with tracer.start_trace(TraceType.EMBEDDING, idea_id=123):
            embedding = await client.create_embedding(
                text="Analyze this startup idea...",
                model="text-embedding-3-small",
            )
    """

    # OpenAI embedding pricing (per 1M tokens as of 2025)
    PRICING = {
        "text-embedding-3-small": 0.02,
        "text-embedding-3-large": 0.13,
        "text-embedding-ada-002": 0.10,
        "default": 0.02,
    }

    def __init__(
        self,
        tracer: Tracer,
        client: Optional[AsyncOpenAI] = None,
        api_key: Optional[str] = None,
        execution_enabled: bool = False,
    ):
        """Initialize the traced OpenAI client.

        Args:
            tracer: The Tracer instance to use for tracing
            client: Optional existing AsyncOpenAI client
            api_key: Optional API key (used if client not provided)
            execution_enabled: Explicit authorization for external provider calls
        """
        self.tracer = tracer
        self.execution_enabled = validate_execution_enabled(execution_enabled)
        self.client: Optional[AsyncOpenAI] = None
        if self.execution_enabled is True:
            self.client = client if client is not None else AsyncOpenAI(api_key=api_key)

    def _require_client(self) -> AsyncOpenAI:
        """Return the client only when external provider execution is authorized."""
        require_provider_execution(self.execution_enabled, provider="OpenAI")
        if self.client is None:
            raise RuntimeError("OpenAI client is unavailable")
        return self.client

    async def create_embedding(
        self,
        text: str,
        *,
        model: str = "text-embedding-3-small",
        dimensions: Optional[int] = None,
        span_name: str = "openai_embedding",
        metadata: Optional[dict] = None,
    ) -> list[float]:
        """Create an embedding with automatic tracing.

        Args:
            text: The text to embed
            model: Model identifier (e.g., "text-embedding-3-small")
            dimensions: Optional dimensions for the embedding
            span_name: Name for the tracing span
            metadata: Optional metadata for the span

        Returns:
            List of floats representing the embedding vector

        Raises:
            ProviderExecutionDisabledError: If provider execution is disabled
        """
        self._require_client()
        ctx = get_current_context()
        if not ctx:
            # No trace context - just make the call without tracing
            return await self._raw_create_embedding(
                text=text,
                model=model,
                dimensions=dimensions,
            )

        # Build input data for tracing
        input_data: dict[str, int | str] = {"text_length": len(text)}
        if self.tracer.capture_prompts is True:
            input_data["text_preview"] = text[:200] + "..." if len(text) > 200 else text
        if dimensions:
            input_data["dimensions"] = dimensions

        async with self.tracer.start_span(
            SpanType.EMBEDDING_CALL,
            span_name,
            provider="openai",
            model=model,
            input_data=input_data,
            metadata=metadata,
        ) as span:
            # start_span persists before yielding; guard that boundary and dispatch separately.
            client = self._require_client()
            response = await client.embeddings.create(
                input=text,
                model=model,
                dimensions=dimensions if dimensions else None,  # type: ignore[arg-type]
            )

            # Extract embedding
            embedding = response.data[0].embedding

            # Record token usage
            span.record_tokens(response.usage.prompt_tokens, 0)
            span.record_output(
                {
                    "model": response.model,
                    "embedding_dimensions": len(embedding),
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                }
            )

            return embedding

    async def create_embeddings_batch(
        self,
        texts: list[str],
        *,
        model: str = "text-embedding-3-small",
        dimensions: Optional[int] = None,
        span_name: str = "openai_embedding_batch",
        metadata: Optional[dict] = None,
    ) -> list[list[float]]:
        """Create embeddings for multiple texts with automatic tracing.

        Args:
            texts: List of texts to embed
            model: Model identifier
            dimensions: Optional dimensions for embeddings
            span_name: Name for the tracing span
            metadata: Optional metadata for the span

        Returns:
            List of embedding vectors (one per input text)
        """
        self._require_client()
        ctx = get_current_context()
        if not ctx:
            return await self._raw_create_embeddings_batch(
                texts=texts,
                model=model,
                dimensions=dimensions,
            )

        input_data = {
            "batch_size": len(texts),
            "total_text_length": sum(len(t) for t in texts),
        }
        if dimensions:
            input_data["dimensions"] = dimensions

        async with self.tracer.start_span(
            SpanType.EMBEDDING_CALL,
            span_name,
            provider="openai",
            model=model,
            input_data=input_data,
            metadata=metadata,
        ) as span:
            # start_span persists before yielding; guard that boundary and dispatch separately.
            client = self._require_client()
            response = await client.embeddings.create(
                input=texts,
                model=model,
                dimensions=dimensions if dimensions else None,  # type: ignore[arg-type]
            )

            # Sort by index to maintain order
            embeddings = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

            span.record_tokens(response.usage.prompt_tokens, 0)
            span.record_output(
                {
                    "model": response.model,
                    "batch_size": len(embeddings),
                    "embedding_dimensions": len(embeddings[0]) if embeddings else 0,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                }
            )

            return embeddings

    async def _raw_create_embedding(
        self,
        text: str,
        model: str,
        dimensions: Optional[int],
    ) -> list[float]:
        """Make the raw API call without tracing."""
        client = self._require_client()
        response = await client.embeddings.create(
            input=text,
            model=model,
            dimensions=dimensions if dimensions else None,  # type: ignore[arg-type]
        )
        return response.data[0].embedding

    async def _raw_create_embeddings_batch(
        self,
        texts: list[str],
        model: str,
        dimensions: Optional[int],
    ) -> list[list[float]]:
        """Make the raw batch API call without tracing."""
        client = self._require_client()
        response = await client.embeddings.create(
            input=texts,
            model=model,
            dimensions=dimensions if dimensions else None,  # type: ignore[arg-type]
        )
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]

    def calculate_cost(self, model: str, tokens: int) -> float:
        """Calculate estimated cost in USD.

        Args:
            model: Model identifier
            tokens: Number of tokens processed

        Returns:
            Estimated cost in USD
        """
        price_per_million = self.PRICING.get(model, self.PRICING["default"])
        return (tokens / 1_000_000) * price_per_million
