"""Groq provider.

Groq runs Llama and other open models on custom LPU hardware — extremely fast.
Uses the OpenAI-compatible API at api.groq.com/openai/v1.

Free tier: ~14,400 requests/day per model.
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
from packages.core.config import get_settings

GROQ_BASE = "https://api.groq.com/openai/v1"
DEFAULT_TIMEOUT = 60.0


class GroqProvider(ModelProvider):
    name = "groq"

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        key = api_key or getattr(settings, "groq_api_key", None)
        if not key:
            raise ProviderError("GROQ_API_KEY not configured")
        self._api_key = key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
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
        reasoning_effort: str | None = None,
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
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}

        # S.6.4.10 — gpt-oss models are reasoning models. Left at their
        # default, they spend the whole max_tokens budget on hidden
        # reasoning tokens and return empty content with
        # finish_reason='length'. Observed: out_tokens=6000, text_len=0,
        # finish_reason=length for asset_classes, market_analysis,
        # fundamental_metrics, class_room. reasoning_effort='low' caps
        # the reasoning budget so the visible answer fits. Confirmed by
        # Groq's docs and other integrations (see console.groq.com/docs/reasoning).
        if model.startswith("openai/gpt-oss"):
            # S.6.4.10 — reasoning models spend budget on hidden reasoning.
            # The right effort depends on the task: 'low' for fast
            # classification, 'medium' or 'high' for article writing.
            # Configurable so we don't have to edit code to tune.
            import os as _os
            # S.8.44 — default 'low'. At 'medium', gpt-oss-20b and
            # gpt-oss-120b both consume most of the max_tokens budget
            # on hidden reasoning before writing. Observed on
            # market_analysis: out_tokens=9000, text_len=0,
            # finish_reason=length on gpt-oss-20b. 'low' leaves more
            # budget for visible output. Override via env if needed.
            # S.8.49.13 — per-call override. Verification passes
            # "medium" so the verifier reliably applies its own
            # directive's permitted clauses; generation uses the
            # env default (currently "low").
            payload["reasoning_effort"] = (
                reasoning_effort
                or _os.environ.get("GROQ_REASONING_EFFORT", "low")
            )

        # S.6.2.0 — no internal 429 retry. The router rotates credentials
        # on 429 and a per-provider sleep here would block that rotation.
        # Fail fast; the router decides whether to try the next credential.
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                r = await client.post(
                    f"{GROQ_BASE}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Groq connection failed: {exc}") from exc

        # DEBUG: print the full request and response to stdout.
        # Visible in `docker compose logs api` and stdout capture.
        import os as _os_dbg
        if _os_dbg.environ.get("GROQ_DEBUG", "1") == "1":
            import json as _json
            print("=" * 80)
            print("=== GROQ REQUEST ===")
            print(_json.dumps(payload, indent=2, default=str))
            print("=== GROQ RESPONSE ===")
            print(r.text)
            print("=" * 80)

        import os as _os_dbg
        if _os_dbg.environ.get("GROQ_DEBUG", "1") == "1":
            import json as _json
            print("=" * 80)
            print("=== GROQ REQUEST ===")
            print(_json.dumps(payload, indent=2, default=str))
            print("=== GROQ RESPONSE ===")
            print(r.text)
            print("=" * 80)

        if r.status_code != 200:
            raise ProviderError(f"Groq HTTP {r.status_code}: {r.text[:400]}")

        data = r.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Groq malformed response: {data}") from exc

        usage = data.get("usage") or {}
        rate_headers = {
            k.lower(): v for k, v in r.headers.items()
            if k.lower().startswith("x-ratelimit-")
        }
        return CompletionResult(
            text=text,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            raw={},
            rate_headers=rate_headers or None,
            finish_reason=finish_reason,
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        raise ProviderError("Groq embeddings not implemented — use Ollama's nomic-embed-text")
