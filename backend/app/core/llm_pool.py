"""
Shared LLM instance pool for DocuMind AI agent system.

Supports multiple backends:
- OpenAI or any OpenAI-compatible endpoint (ChatOpenAI) — set OPENAI_BASE_URL to point
  at Groq, OpenRouter, etc. Leave unset for real OpenAI (production, highest quality).
- Ollama (ChatOllama) — local, free, private
- Mock (FakeListLLM) — development/testing, zero cost

Prevents duplicate instances, reduces memory footprint,
and centralizes config management.

Usage:
    from app.core.llm_pool import get_llm
    llm = get_llm(streaming=False)  # For fast inference
    llm_stream = get_llm(streaming=True)  # For token streaming
"""

from __future__ import annotations

import logging
from urllib.error import URLError
from urllib.request import urlopen
from functools import lru_cache
from typing import Literal

from langchain_core.language_models import BaseChatModel

from app.config import get_settings

logger = logging.getLogger(__name__)
# time causes import-time crashes when env vars aren't set (common in tests/CI), and
# produces a stale reference if settings change between calls. get_llm() now calls
# get_settings() inline so LRU cache always uses current config.


def _get_mock_llm() -> BaseChatModel:
    """Return a chat-compatible mock LLM for offline development."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    return FakeListChatModel(
        responses=[
            "[DEV MODE] This is a mock response. Set OPENAI_API_KEY or install Ollama for real inference.",
            "[DEV MODE] Query received. Add billing to OpenAI or run 'ollama pull llama3.2' for real responses.",
            "[DEV MODE] Mock answer: DocuMind AI is a document intelligence platform.",
        ]
    )


@lru_cache(maxsize=4)  # Cache up to 4 configs (openai/ollama/mock × streaming on/off)
def get_llm(
    streaming: bool = False,
    model_override: str | None = None,
    temperature_override: float | None = None,
    provider_override: Literal["openai", "ollama", "mock"] | None = None,
) -> BaseChatModel:
    """
    Get cached LLM instance with standardized config.

    DVMELTSS-M: Single responsibility — only creates/configures LLMs.
    ASCALE-C: Loose coupling — modules import this instead of creating their own.

    Args:
        streaming: Enable token streaming for response generation.
        model_override: Optional model name to override config default.
        temperature_override: Optional temperature to override defaults.
        provider_override: Force specific provider (openai/ollama/mock).

    Returns:
        Configured BaseChatModel instance (cached via LRU).

    Raises:
        RuntimeError: If no valid provider can be initialized.
    """
    # vars before importing, and settings changes are always reflected
    _settings = get_settings()

    # Determine provider priority
    provider = (
        provider_override or getattr(_settings, "llm_provider", "openai")  # Default to openai
    )

    model = model_override or getattr(_settings, "openai_chat_model", "gpt-4o-mini")
    temperature = temperature_override or (0.1 if streaming else 0.0)

    logger.debug(f"Initializing LLM: provider={provider}, model={model}, streaming={streaming}, temp={temperature}")

    # -- 1. Try Ollama (local, free, primary) --------------------
    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama

            ollama_model = model_override or getattr(_settings, "ollama_model", "llama3.2:7b")
            base_url = getattr(_settings, "ollama_base_url", "http://localhost:11434")
            try:
                with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=1.0):
                    pass
            except (OSError, URLError, TimeoutError) as e:
                raise RuntimeError(f"Ollama unavailable at {base_url}: {e}") from e

            llm = ChatOllama(
                model=ollama_model,
                base_url=base_url,
                temperature=temperature,
                streaming=streaming,
            )
            logger.info(f"Using Ollama LLM: {ollama_model} @ {base_url}")
            return llm
        except ImportError:
            logger.warning("langchain-ollama not installed. Falling back to OpenAI.")
        except Exception as e:
            logger.warning(f"Ollama unavailable: {e}. Falling back to OpenAI.")

        # Ollama failed → try Groq (free cloud) as first cloud fallback
        groq_key = getattr(_settings, "groq_api_key", None)
        if groq_key:
            try:
                from langchain_openai import ChatOpenAI

                groq_model = getattr(_settings, "groq_model", "llama-3.3-70b-versatile")
                llm = ChatOpenAI(
                    model=groq_model,
                    api_key=groq_key,
                    base_url="https://api.groq.com/openai/v1",
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    request_timeout=30,
                    max_tokens=4096,
                )
                logger.info(f"Ollama unavailable — using Groq fallback: {groq_model}")
                return llm
            except Exception as e:
                logger.warning(f"Groq fallback failed: {e}. Trying OpenAI.")

        # Groq failed (or no key) → try OpenAI as second cloud fallback
        api_key = getattr(_settings, "openai_api_key", None)
        if api_key:
            try:
                from langchain_openai import ChatOpenAI

                llm = ChatOpenAI(
                    model=getattr(_settings, "openai_chat_model", "gpt-4o"),
                    api_key=api_key,
                    base_url=getattr(_settings, "openai_base_url", None),
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=3,
                    request_timeout=getattr(_settings, "llm_request_timeout", 30),
                    max_tokens=getattr(_settings, "llm_max_tokens", 4096),
                )
                logger.info(f"Ollama+Groq unavailable — using OpenAI fallback: {llm.model_name}")
                return llm
            except Exception as e:
                logger.error(f"OpenAI fallback also failed: {e}. Using mock LLM.")
        else:
            logger.warning("Ollama unavailable, no GROQ_API_KEY, no OPENAI_API_KEY. Using mock LLM.")
        return _get_mock_llm()

    # -- 2. Try OpenAI or an OpenAI-compatible provider (Groq, etc.) --
    if provider == "openai":
        api_key = getattr(_settings, "openai_api_key", None)

        if api_key:
            try:
                from langchain_openai import ChatOpenAI

                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=getattr(_settings, "openai_base_url", None),
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=3,
                    request_timeout=getattr(_settings, "llm_request_timeout", 30),
                    max_tokens=getattr(_settings, "llm_max_tokens", 4096),
                )
                base_url = getattr(_settings, "openai_base_url", None)
                logger.info(f"Using {'OpenAI-compatible' if base_url else 'OpenAI'} LLM: {model}" + (f" @ {base_url}" if base_url else ""))
                return llm
            except Exception as e:
                logger.error(f"OpenAI-compatible LLM initialization failed: {e}")
        else:
            logger.warning("OPENAI_API_KEY not set. Skipping OpenAI provider.")

    # -- 3. Fallback: Mock LLM for development -------------------
    logger.warning("Using mock chat LLM for development (no valid LLM provider available)")
    try:
        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        return FakeListChatModel(
            responses=[
                "[DEV MODE] This is a mock response. Set OPENAI_API_KEY or install Ollama for real inference.",
                "[DEV MODE] Query received. Add billing to OpenAI or run 'ollama pull llama3.2' for real responses.",
                "[DEV MODE] Mock answer: DocuMind AI is a document intelligence platform.",
            ]
        )
    except ImportError:
        # Last resort: minimal mock
        logger.error("No LLM backend available. Install langchain-core fake chat models or configure a provider.")
        raise RuntimeError(
            "No LLM provider available. Options:\n"
            "1. Set OPENAI_API_KEY in .env\n"
            "2. Install & run Ollama: pip install langchain-ollama + 'ollama pull llama3.2'\n"
            "3. Install mock fallback: pip install langchain-community"
        )


def clear_llm_cache() -> None:
    """Clear LRU cache — useful for testing config changes."""
    get_llm.cache_clear()
    logger.info("LLM pool cache cleared.")


# ── Per-workspace BYOK resolution ───────────────────────────────────────────
# Separate from get_llm()'s @lru_cache: workspace keys change over time via the
# /api/v1/llm-settings routes, so correctness (picking up updates) matters more than
# raw cache simplicity. Keyed by (workspace_id, streaming) -> (updated_at, llm instance),
# auto-invalidated whenever the DB row's updated_at changes.
_workspace_llm_cache: dict[tuple[str, bool], tuple] = {}


async def get_llm_for_workspace(workspace_id: str, streaming: bool = False) -> BaseChatModel:
    """
    Resolve the LLM to use for a given workspace, honoring per-workspace BYOK config
    if one is set, otherwise falling back to the platform-wide default (get_llm()).
    """
    from app.core.workspace_llm_config import get_workspace_llm_config

    try:
        config = await get_workspace_llm_config(workspace_id)
    except Exception as e:
        logger.warning(f"Workspace LLM config lookup failed for {workspace_id}: {e}. Using platform default.")
        config = None

    if config is None:
        return get_llm(streaming=streaming)

    cache_key = (workspace_id, streaming)
    cached = _workspace_llm_cache.get(cache_key)
    if cached is not None and cached[0] == config.updated_at:
        return cached[1]

    temperature = 0.1 if streaming else 0.0

    if config.provider == "ollama":
        from langchain_ollama import ChatOllama

        llm: BaseChatModel = ChatOllama(
            model=config.model,
            base_url=config.base_url or "http://localhost:11434",
            temperature=temperature,
            streaming=streaming,
        )
    else:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=config.model,
            api_key=config.api_key,
            base_url=config.base_url,
            temperature=temperature,
            streaming=streaming,
            max_retries=3,
        )

    _workspace_llm_cache[cache_key] = (config.updated_at, llm)
    logger.info(f"Workspace {workspace_id} using BYOK LLM: provider={config.provider}, model={config.model}")
    return llm


# DVMELTSS-T: Test-only helper to force recreate instances
def _recreate_llm_for_test(
    streaming: bool = False,
    provider: Literal["openai", "ollama", "mock"] = "mock",
) -> BaseChatModel:
    """Bypass cache for testing — DO NOT USE IN PRODUCTION."""
    clear_llm_cache()
    return get_llm(streaming=streaming, provider_override=provider)


# DVMELTSS-M: Explicit module exports
__all__ = ["get_llm", "get_llm_for_workspace", "clear_llm_cache", "_recreate_llm_for_test"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.llm_pool) -------
# ========================================================================

