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
    """Return a dev-only mock LLM.  Raises in production — never silently serve fake answers."""
    settings = get_settings()
    env = getattr(settings, "environment", "dev")
    if env not in ("dev", "test", "development", "testing"):
        raise RuntimeError(
            f"No real LLM provider available in '{env}' environment. "
            "Configure OPENAI_API_KEY, GROQ_API_KEY (via OPENAI_BASE_URL), "
            "or a reachable Ollama endpoint (OLLAMA_BASE_URL). "
            "FakeListChatModel is disabled outside dev/test environments."
        )

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

        # Ollama failed → try Gemini Flash (free, 1M context) as first cloud fallback
        gemini_key = getattr(_settings, "gemini_api_key", None)
        if gemini_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                gemini_model = getattr(_settings, "gemini_model", "gemini-2.0-flash")
                llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    google_api_key=gemini_key,
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    max_output_tokens=8192,
                )
                logger.info(f"Ollama unavailable — using Gemini fallback: {gemini_model}")
                return llm
            except Exception as e:
                logger.warning(f"Gemini fallback failed: {e}. Trying Groq.")

        # Gemini failed (or no key) → try Groq (free cloud) as second cloud fallback
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

        # Groq failed (or no key) → try OpenRouter (free models) as second cloud fallback
        openrouter_key = getattr(_settings, "openrouter_api_key", None)
        if openrouter_key:
            try:
                from langchain_openai import ChatOpenAI

                openrouter_model = getattr(
                    _settings, "openrouter_model", "nvidia/nemotron-ultra-253b-v1:free"
                )
                llm = ChatOpenAI(
                    model=openrouter_model,
                    api_key=openrouter_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    request_timeout=60,
                    max_tokens=4096,
                    default_headers={
                        "HTTP-Referer": "https://docmind-backend-4ip1.onrender.com",
                        "X-Title": "DocMind AI",
                    },
                )
                logger.info(f"Using OpenRouter fallback: {openrouter_model}")
                return llm
            except Exception as e:
                logger.warning(f"OpenRouter fallback failed: {e}. Trying OpenAI.")

        # OpenRouter failed (or no key) → try OpenAI as third cloud fallback
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
                logger.error(f"OpenAI fallback also failed: {e}. Falling back to mock LLM (dev only).")
        else:
            logger.warning("Ollama unavailable, no GROQ_API_KEY, no OPENAI_API_KEY. Falling back to mock LLM (dev only).")
        return _get_mock_llm()

    # -- 2. Try Gemini as standalone provider (LLM_PROVIDER=gemini) ------
    if provider == "gemini":
        gemini_key = getattr(_settings, "gemini_api_key", None)
        if gemini_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                gemini_model = getattr(_settings, "gemini_model", "gemini-2.0-flash")
                llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    google_api_key=gemini_key,
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    max_output_tokens=8192,
                )
                logger.info(f"Using Gemini: {gemini_model}")
                return llm
            except Exception as e:
                logger.error(f"Gemini initialization failed: {e}")
        else:
            logger.warning("GEMINI_API_KEY not set. Skipping gemini provider.")
        return _get_mock_llm()

    # -- 3. Try OpenAI / OpenRouter / any OpenAI-compatible provider --
    if provider == "openai":
        api_key = getattr(_settings, "openai_api_key", None)
        openrouter_key = getattr(_settings, "openrouter_api_key", None)
        gemini_key = getattr(_settings, "gemini_api_key", None)

        # Prefer OPENAI_API_KEY if set
        if api_key:
            try:
                from langchain_openai import ChatOpenAI

                base_url = getattr(_settings, "openai_base_url", None)
                extra_headers = {}
                if base_url and "openrouter.ai" in base_url:
                    extra_headers = {
                        "HTTP-Referer": "https://docmind-backend-4ip1.onrender.com",
                        "X-Title": "DocMind AI",
                    }
                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=3,
                    request_timeout=getattr(_settings, "llm_request_timeout", 60),
                    max_tokens=getattr(_settings, "llm_max_tokens", 4096),
                    default_headers=extra_headers or None,
                )
                logger.info(f"Using {'OpenRouter' if 'openrouter' in (base_url or '') else 'OpenAI-compatible' if base_url else 'OpenAI'} LLM: {model}" + (f" @ {base_url}" if base_url else ""))
                return llm
            except Exception as e:
                logger.error(f"OpenAI-compatible LLM initialization failed: {e}")

        # No OPENAI_API_KEY → try Gemini Flash (free, 1M context)
        elif gemini_key:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                gemini_model = getattr(_settings, "gemini_model", "gemini-2.0-flash")
                llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    google_api_key=gemini_key,
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    max_output_tokens=8192,
                )
                logger.info(f"Using Gemini (openai path fallback): {gemini_model}")
                return llm
            except Exception as e:
                logger.error(f"Gemini initialization failed: {e}")

        # Fallback to OPENROUTER_API_KEY if no OPENAI_API_KEY / Gemini
        elif openrouter_key:
            try:
                from langchain_openai import ChatOpenAI

                openrouter_model = getattr(
                    _settings, "openrouter_model", "nvidia/nemotron-ultra-253b-v1:free"
                )
                llm = ChatOpenAI(
                    model=openrouter_model,
                    api_key=openrouter_key,
                    base_url="https://openrouter.ai/api/v1",
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=2,
                    request_timeout=60,
                    max_tokens=4096,
                    default_headers={
                        "HTTP-Referer": "https://docmind-backend-4ip1.onrender.com",
                        "X-Title": "DocMind AI",
                    },
                )
                logger.info(f"Using OpenRouter (openai provider path): {openrouter_model}")
                return llm
            except Exception as e:
                logger.error(f"OpenRouter initialization failed: {e}")
        else:
            logger.warning("OPENAI_API_KEY and OPENROUTER_API_KEY not set. Skipping openai provider.")

    # -- 3. Fallback: Mock LLM for development only -------------------
    logger.warning("No valid LLM provider configured — falling back to mock LLM (dev/test only)")
    return _get_mock_llm()


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

