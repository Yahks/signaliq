"""Ollama provider. Talks to the local Ollama daemon at localhost:11434.

NOTE: This is currently the ONLY provider that implements
embed(), and it's the only active model for the `embedding` role.
If this provider is down, clustering and semantic search break.

Why no cloud fallback:
  - gemini_provider, groq_provider, openrouter_provider all raise
    'embeddings not implemented'
  - Adding a cloud fallback would require:
      (1) An embedding API client for that provider
      (2) Dimension compatibility with the pinned vector() column
          (nomic-embed-text = 768 dims)
      (3) Possibly a schema migration + full re-embed
  - Ollama failures are local-process failures, not cloud rate
    limits. `systemctl restart ollama` is the fix.

If we ever add a cloud embedding fallback, Gemini's
text-embedding-004 (768 dims) is the natural candidate."""
from __future__ import annotations

import json
from typing import Any

import httpx

from packages.ai.providers.base import (
    CompletionResult,
    EmbeddingResult,
    ModelProvider,
    ProviderError,
)

import os
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
DEFAULT_TIMEOUT = 300.0  # CPU-only inference can be slow


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str = OLLAMA_BASE) -> None:
        self.base_url = base_url.rstrip("/")

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
    ) -> CompletionResult:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if response_format_json:
            payload["format"] = "json"

        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                r = await client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama connection failed: {exc}") from exc

        if r.status_code != 200:
            raise ProviderError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")

        data = r.json()
        message = data.get("message") or {}
        text = message.get("content", "")

        # Ollama reports token counts when available
        in_tok = data.get("prompt_eval_count")
        out_tok = data.get("eval_count")

        return CompletionResult(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            raw=data,
            finish_reason=data.get("done_reason"),
        )

    async def embed(self, *, model: str, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=[], dim=0)

        vectors: list[list[float]] = []
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                for text in texts:
                    r = await client.post(
                        f"{self.base_url}/api/embeddings",
                        json={"model": model, "prompt": text},
                    )
                    if r.status_code != 200:
                        raise ProviderError(f"Ollama HTTP {r.status_code}: {r.text[:300]}")
                    data = r.json()
                    vectors.append(data["embedding"])
        except httpx.HTTPError as exc:
            raise ProviderError(f"Ollama embed failed: {exc}") from exc

        dim = len(vectors[0]) if vectors else 0
        return EmbeddingResult(vectors=vectors, dim=dim)
