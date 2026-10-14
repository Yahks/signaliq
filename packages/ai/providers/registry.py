"""Provider registry: name → instance. Enables provider switching via config."""
from __future__ import annotations

from functools import lru_cache

from packages.ai.providers.base import ModelProvider
from packages.ai.providers.gemini_provider import GeminiProvider
from packages.ai.providers.groq_provider import GroqProvider
from packages.ai.providers.ollama_provider import OllamaProvider
from packages.ai.providers.openrouter_provider import OpenRouterProvider


@lru_cache(maxsize=8)
def get_provider(name: str) -> ModelProvider:
    if name == "ollama":
        return OllamaProvider()
    if name == "gemini":
        return GeminiProvider()
    if name == "openrouter":
        return OpenRouterProvider()
    if name == "groq":
        return GroqProvider()
    raise ValueError(f"Unknown provider: {name}")
