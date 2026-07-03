# backend/app/core/llm_providers.py
"""
Registry of supported LLM providers for per-workspace BYOK configuration.

Only OpenAI-API-compatible providers are listed — they all work through the existing
ChatOpenAI(base_url=...) override (see app.core.llm_pool). Anthropic Claude is not
OpenAI-compatible and would need langchain_anthropic + a separate branch — deliberately
left out of this registry until there's demand for it.
"""

from __future__ import annotations

PROVIDER_REGISTRY: dict[str, dict[str, str | None]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": None,
        "default_model": "gpt-4o",
    },
    "groq": {
        "label": "Groq (Free)",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.0-flash",
    },
    "ollama": {
        "label": "Ollama (Local)",
        "base_url": "http://localhost:11434",
        "default_model": "llama3.2:7b",
    },
}


__all__ = ["PROVIDER_REGISTRY"]
