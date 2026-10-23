"""OpenRouter provider.

OpenRouter exposes an OpenAI-compatible API at openrouter.ai/api/v1.
One API key → dozens of models (Gemini, Llama, Claude, GPT, DeepSeek, Qwen).

Free tier models use the `:free` suffix in the model name, e.g.
  google/gemini-2.0-flash-exp:free
  meta-llama/llama-3.3-70b-instruct:free
"""
from __future__ import annotations

from typing import Any

import httpx

from packages.ai.providers.base import (
    CompletionResult,
    EmbeddingResult,
    ModelProvider,
    ProviderError,
)
from packages.core.error import CredentialsExhaustedError
from packages.core.config import get_settings

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 120.0


class OpenRouterProvider(ModelProvider):
    name = "openrouter"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or getattr(settings, "openrouter_api_key", None)
        if not key:
            raise ProviderError("OPENROUTER_API_KEY not configured")
        self._api_key = key
        self._base = OPENROUTER_BASE

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            # Optional but recommended by OpenRouter:
            "HTTP-Referer": "https://signaliq.local",
            "X-Title": "SignalIQ",
        }

    async def complete(
        self,
        *,
        model: str,
        system: str | None,
        user: str,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        response_format_json: bool = False,
        images: list[bytes] | None = None,  # accepted + ignored
        reasoning_effort: str | None = None,  # accepted + ignored
    ) -> CompletionResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        # S.8.28 — reasoning models on OpenRouter have the same failure
        # mode the groq provider documents for gpt-oss: they spend the
        # whole max_tokens budget on hidden reasoning and return empty
        # content with finish_reason='length'. Observed repeatedly with
        # nex-agi/nex-n2.5-mini: out_tokens=9000, text_len=0.
        #
        # Two safeguards:
        #   1. Cap max_tokens at a bounded value (default 4000). The
        #      caller passes 9000 (the Groq budget), which is more than
        #      this provider needs and more than the fallback models
        #      can use productively. 4000 is enough for a 350-word
        #      article plus a short reasoning trace, and small enough
        #      that a runaway cannot burn the full budget on hidden
        #      reasoning with no visible content.
        #   2. reasoning_effort='low' requests a small reasoning budget.
        #      Honored by some models, ignored by others — the cap
        #      above is the durable safeguard.
        #
        # Both are environment-configurable so the values can be tuned
        # without a code change.
        import os as _os
        OPENROUTER_MAX_TOKENS = int(
            _os.environ.get("OPENROUTER_MAX_TOKENS", "4000")
        )
        _effective_max = (
            max_tokens if max_tokens is not None else OPENROUTER_MAX_TOKENS
        )
        if _effective_max > OPENROUTER_MAX_TOKENS:
            _effective_max = OPENROUTER_MAX_TOKENS
        payload["max_tokens"] = _effective_max

        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        payload["reasoning_effort"] = _os.environ.get(
            "OPENROUTER_REASONING_EFFORT", "low"
        )

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                r = await client.post(
                    f"{self._base}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenRouter connection failed: {exc}") from exc

        if r.status_code != 200:
            # Transient failures (rate limit, service down) should
            # rotate to the next credential, not abort the run.
            _body = r.text[:400].lower()
            _is_transient = (
                r.status_code == 429
                or r.status_code == 503
                or "rate limit" in _body
                or "resource_exhausted" in _body
                or "quota exceeded" in _body
                or "high demand" in _body
                or "unavailable" in _body
            )
            if _is_transient:
                raise CredentialsExhaustedError(
                    f"OpenRouter HTTP {r.status_code}: {r.text[:200]}"
                )
            raise ProviderError(f"OpenRouter HTTP {r.status_code}: {r.text[:400]}")

        data = r.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"OpenRouter malformed response: {data}") from exc

        usage = data.get("usage") or {}
        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
            finish_reason=finish_reason,
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        raise ProviderError("OpenRouter embeddings not implemented — use Ollama's nomic-embed-text")
