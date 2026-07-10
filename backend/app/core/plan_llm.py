"""
Maps a workspace plan to the appropriate LLM configuration.

Tiering:
  free     → Groq llama-3.1-8b-instant  (fast, cheap, rate-limited)
  starter  → Groq llama-3.3-70b-versatile (full quality, cloud)
  pro      → Groq llama-3.3-70b-versatile OR OpenAI gpt-4o if key present
  enterprise → OpenAI gpt-4o (or BYOK from workspace settings)

Falls back to llm_pool.get_llm() if no plan-specific override is possible.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Map llm_tier → (groq_model, openai_model_fallback)
_TIER_MODELS: dict[str, tuple[str, str]] = {
    "basic":    ("llama-3.1-8b-instant",     "gpt-4o-mini"),
    "standard": ("llama-3.3-70b-versatile",  "gpt-4o-mini"),
    "advanced": ("llama-3.3-70b-versatile",  "gpt-4o"),
}


def get_llm_for_plan(plan: str, byok_key: Optional[str] = None):
    """
    Return a LangChain chat model appropriate for the given plan.

    Falls back to the global llm_pool.get_llm() chain on any error.
    """
    from app.config import get_settings
    from app.core.plan_registry import PLAN_REGISTRY

    settings = get_settings()
    tier = PLAN_REGISTRY.get(plan, {}).get("llm_tier", "basic")
    groq_model, openai_model = _TIER_MODELS.get(tier, _TIER_MODELS["basic"])

    # Enterprise / pro with BYOK → OpenAI
    if byok_key or (tier == "advanced" and settings.openai_api_key):
        try:
            from langchain_openai import ChatOpenAI
            key = byok_key or settings.openai_api_key
            return ChatOpenAI(model=openai_model, api_key=key, temperature=0.1, max_tokens=4096)
        except Exception as e:
            logger.warning(f"[plan_llm] OpenAI init failed for plan={plan}: {e}")

    # Groq cloud (free tier)
    groq_key = getattr(settings, "groq_api_key", None)
    if groq_key:
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=groq_model,
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
                temperature=0.1,
                max_tokens=4096,
            )
        except Exception as e:
            logger.warning(f"[plan_llm] Groq init failed for plan={plan}: {e}")

    # Last resort — global pool
    from app.core.llm_pool import get_llm
    return get_llm()


__all__ = ["get_llm_for_plan"]
