"""Google Gemini provider. Uses the new google-genai SDK.

Free tier: 15 req/min, 1500 req/day, 1M tokens/day.
Model: gemini-2.5-flash (fast, capable, free).
"""
from __future__ import annotations

import asyncio
from typing import Any

from google import genai
from google.genai import types as genai_types

from packages.ai.providers.base import (
    CompletionResult,
    EmbeddingResult,
    ModelProvider,
    ProviderError,
)
from packages.core.error import CredentialsExhaustedError
from packages.core.config import get_settings


class GeminiProvider(ModelProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        from packages.core.config import get_settings
        s = get_settings()
        key = api_key or getattr(s, "gemini_api_key", None)
        if not key:
            raise ProviderError("Gemini API key not configured")
        from google import genai
        self._client = genai.Client(api_key=key)

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
        # S.8.88 — accepted for router compatibility; Gemini does not
        # use this parameter. Reasoning budget is controlled by
        # thinking_budget below. Without this kwarg the router raises
        # TypeError on every Gemini-routed call, silently dropping
        # verify batches.
        reasoning_effort: str | None = None,
    ) -> CompletionResult:
        # Gemini 3.x is a thinking model — output tokens are consumed by
        # internal reasoning before visible text. Two safeguards:
        #   1. thinking_budget=0 disables extended thinking (our tasks are
        #      short: verify + narrate, no chain-of-thought needed)
        #   2. Floor max_output_tokens at 1500 in case #1 is ignored by the
        #      SDK — gives room to think AND answer, avoiding MAX_TOKENS
        #      with no visible text.
        effective_max = max(max_tokens or 1500, 1500)

        config_kwargs: dict[str, Any] = {"temperature": temperature}
        config_kwargs["max_output_tokens"] = effective_max
        if response_format_json:
            config_kwargs["response_mime_type"] = "application/json"
        if system:
            config_kwargs["system_instruction"] = system

        try:
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=0,
            )
        except (AttributeError, TypeError):
            # Older SDK — no thinking control available
            pass

        config = genai_types.GenerateContentConfig(**config_kwargs)

        # S.8.11.3 — build the contents. When images are supplied,
        # pass a list of parts: a text part for the prompt, then one
        # inline-data part per image. Gemini sniffs the MIME type from
        # the magic bytes; the inline_data variant is what the SDK
        # accepts for raw bytes.
        if images:
            from google.genai import types as _gtypes
            parts: list[Any] = []
            if user:
                parts.append(_gtypes.Part.from_text(text=user))
            for img in images:
                parts.append(
                    _gtypes.Part.from_bytes(data=img, mime_type="image/png")
                )
            contents: Any = parts
        else:
            contents = user

        def _call() -> Any:
            return self._client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

        try:
            response = await asyncio.to_thread(_call)
        except Exception as exc:
            # Detect transient provider failures so the router
            # rotates to the next credential instead of aborting
            # the entire generation. Two classes:
            #   - 429 / RESOURCE_EXHAUSTED — rate limit
            #   - 503 / UNAVAILABLE / "high demand" — service down
            # Both mean "this credential can't serve right now,
            # try another." The router treats them identically.
            _msg = str(exc).lower()
            _is_transient = (
                "429" in _msg
                or "resource_exhausted" in _msg
                or "quota exceeded" in _msg
                or "rate limit" in _msg
                or "503" in _msg
                or "unavailable" in _msg
                or "high demand" in _msg
            )
            if _is_transient:
                raise CredentialsExhaustedError(
                    f"Gemini transient failure: {str(exc)[:200]}"
                ) from exc
            raise ProviderError(f"Gemini call failed: {exc}") from exc

        # Robust text extraction. Gemini 3.x may return:
        #   - A valid .text property (normal case)
        #   - candidates with only thought parts (no text)
        #   - candidates == None (safety block)
        #   - candidates[0].content == None (partial block)
        # S.8.11.12 — cands is referenced both inside the "no text"
        # fallback and in the return value's finish_reason. Extract
        # it unconditionally so the return never hits UnboundLocalError.
        cands = getattr(response, "candidates", None) or []

        text = getattr(response, "text", None)
        if not text:
            text = ""
            for cand in cands:
                content = getattr(cand, "content", None)
                if content is None:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    t = getattr(part, "text", None)
                    if t:
                        text += t
            if not text:
                # Empty text with finish_reason=MAX_TOKENS (or
                # LENGTH) means the model burned the budget on
                # hidden reasoning and produced nothing visible.
                # That is a quota-style failure — rotate to the
                # next credential instead of crashing. Any other
                # empty-text case (safety block, malformed
                # response) stays a hard ProviderError.
                finish = "unknown"
                if cands:
                    finish = str(getattr(cands[0], "finish_reason", "unknown"))
                prompt_feedback = getattr(response, "prompt_feedback", None)
                _finish_lower = finish.lower()
                _is_length = (
                    "max_tokens" in _finish_lower
                    or "length" in _finish_lower
                )
                if _is_length:
                    raise CredentialsExhaustedError(
                        f"Gemini empty response (finish_reason={finish}) "
                        f"— reasoning budget exhausted, rotating"
                    )
                raise ProviderError(
                    f"Gemini returned no text "
                    f"(finish_reason={finish}, "
                    f"prompt_feedback={prompt_feedback})"
                )

        usage = getattr(response, "usage_metadata", None)
        in_tok = getattr(usage, "prompt_token_count", None) if usage else None
        out_tok = getattr(usage, "candidates_token_count", None) if usage else None

        return CompletionResult(
            text=text, input_tokens=in_tok, output_tokens=out_tok, raw={},
            finish_reason=str(getattr(cands[0], "finish_reason", None)) if cands else None,
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        raise ProviderError("Gemini embeddings not implemented — use Ollama's nomic-embed-text")
