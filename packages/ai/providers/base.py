"""Provider interface. Every LLM provider (Ollama, OpenAI, Anthropic) implements this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

ModelRole = Literal["fast", "reasoning", "embedding", "vision"]


@dataclass
class CompletionResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    # S.6.4.3 — provider rate-limit headers when available. Groq returns
    # x-ratelimit-* on every response; we store them so the UI can show
    # the user's remaining quota per key. None for providers that don't.
    rate_headers: dict[str, str] | None = None
    # S.6.4.7 — why the model stopped. 'stop' = complete, 'length' =
    # hit max_tokens (truncated), others are provider-specific. The
    # router refuses to cache a response with finish_reason='length'
    # or with empty text; those are written as status='truncated' so
    # the cache-lookup (which filters status='ok') never serves them.
    finish_reason: str | None = None


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    dim: int


class ProviderError(Exception):
    """Raised when the provider fails (connection, timeout, bad response)."""


class ModelProvider(ABC):
    """Abstract provider. Concrete classes: OllamaProvider, OpenAIProvider, ..."""

    name: str

    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        system: str | None,
        user: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        images: list[bytes] | None = None,
    ) -> CompletionResult:
        """Generate a completion.

        `images` is an optional list of raw image bytes. Providers
        that support vision (Gemini today) include them in the
        request; providers that don't are free to ignore the
        parameter. S.8.11.3.
        """

    @abstractmethod
    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        """Generate embeddings for one or more texts."""
