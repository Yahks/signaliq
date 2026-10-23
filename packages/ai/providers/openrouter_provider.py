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
            _os.environ.get("OPENROUTER_MAX_TOKENS", "10000")
        )
        # Force at least OPENROUTER_MAX_TOKENS regardless of what
        # the router passes. Reasoning models burn the budget on
        # hidden thought before visible output; a 2000-token cap
        # produces finish_reason=length with empty content (observed
        # with qwen/qwen3.8-27b during the CPAY article run).
        _requested = max_tokens if max_tokens is not None else OPENROUTER_MAX_TOKENS
        _effective_max = max(_requested, OPENROUTER_MAX_TOKENS)
        payload["max_tokens"] = _effective_max

        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        # Qwen 3.x on OpenRouter uses a separate `reasoning` config
        # block that caps the hidden thinking budget. Without it,
        # qwen burns 10,000+ tokens on reasoning and returns an
        # empty response with finish_reason=length — the
        # reasoning_effort field is not honored by qwen.
        _model_lower = (model or "").lower()
        _is_qwen_like = "qwen" in _model_lower or "nex-agi" in _model_lower

        # The top-level reasoning_effort is a legacy field OpenRouter
        # normalizes to reasoning.effort. It must NOT be sent when
        # the nested `reasoning` block is used, or OpenRouter rejects
        # the pair:
        #   400 Only one of "reasoning.effort" and
        #       "reasoning.max_tokens" can be specified
        # Removing the nested effort alone was insufficient; the
        # top-level field was still being normalized into the same
        # conflict. Send it only for models that do NOT get the
        # nested block.
        if not _is_qwen_like:
            payload["reasoning_effort"] = _os.environ.get(
                "OPENROUTER_REASONING_EFFORT", "low"
            )

        if _is_qwen_like:
            # OpenRouter rejects a reasoning block that specifies
            # BOTH effort and max_tokens:
            #   400 Only one of "reasoning.effort" and
            #       "reasoning.max_tokens" can be specified
            # qwen ignores effort and honors max_tokens (the
            # comment above), so keep max_tokens and drop effort.
            payload["reasoning"] = {
                "max_tokens": int(
                    _os.environ.get(
                        "OPENROUTER_REASONING_MAX_TOKENS", "1024"
                    )
                ),
            }

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

        # Empty body with finish_reason=length means the model
        # burned the entire budget on hidden reasoning and produced
        # nothing visible. This is a failure, not a success —
        # rotate to the next credential.
        _out_tokens = usage.get("completion_tokens")
        _text_str = (text or "").strip()
        if not _text_str or finish_reason == "length":
            raise CredentialsExhaustedError(
                f"OpenRouter empty response: out_tokens={_out_tokens} "
                f"finish_reason={finish_reason} text_len={len(_text_str)}"
            )

        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=_out_tokens,
            raw={},
            finish_reason=finish_reason,
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        raise ProviderError("OpenRouter embeddings not implemented — use Ollama's nomic-embed-text")
