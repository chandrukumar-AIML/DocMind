"""Vision-capable LLM provider helper.

# ADDED: Central import point for routes that need image/table reasoning.
"""

from __future__ import annotations

from typing import Optional

from langchain_core.language_models import BaseChatModel

from app.core.llm_pool import get_llm


def get_vision_llm(
    model_override: Optional[str] = None,
    timeout: float | None = None,
) -> BaseChatModel:
    """Return the configured chat model for vision/extraction tasks."""
    # retries are consistent across RAG, agent, and extraction code paths.
    return get_llm(streaming=False, model_override=model_override)


__all__ = ["get_vision_llm"]
# Local smoke test entry point. Run: python -m

