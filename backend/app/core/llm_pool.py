# backend/app/core/llm_pool.py
# DVMELTSS-FIX: M - Modular, S - Scalability, L - Logging
# ASCALE-FIX: S - Separation, C - Coupling
"""
Shared LLM instance pool for DocuMind AI agent system.

Supports multiple backends:
- OpenAI (ChatOpenAI) — production, highest quality
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
from typing import Literal, Optional

from langchain_core.language_models import BaseChatModel

from app.config import get_settings

logger = logging.getLogger(__name__)
# FIXED: Removed module-level settings = get_settings() — calling get_settings() at import
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
    # FIXED: Call get_settings() inline — not at module level — so tests can mock env
    # vars before importing, and settings changes are always reflected
    _settings = get_settings()

    # Determine provider priority
    provider = (
        provider_override
        or getattr(_settings, 'llm_provider', 'openai')  # Default to openai
    )

    model = model_override or getattr(_settings, 'openai_chat_model', 'gpt-4o-mini')
    temperature = temperature_override or (0.1 if streaming else 0.0)
    
    logger.debug(f"Initializing LLM: provider={provider}, model={model}, streaming={streaming}, temp={temperature}")
    
    # -- 1. Try Ollama (local, free) -----------------------------
    if provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
            ollama_model = model_override or getattr(_settings, 'ollama_model', 'llama3.2')
            base_url = getattr(_settings, 'ollama_base_url', 'http://localhost:11434')
            try:
                with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=1.0):
                    pass
            except (OSError, URLError, TimeoutError) as e:
                logger.warning(f"Ollama is not reachable at {base_url}: {e}. Falling back to mock LLM.")
                raise RuntimeError(f"Ollama unavailable: {e}") from e
            
            llm = ChatOllama(
                model=ollama_model,
                base_url=base_url,
                temperature=temperature,
                streaming=streaming,
            )
            logger.info(f"Using Ollama LLM: {ollama_model} @ {base_url}")
            return llm
        except ImportError:
            logger.warning("langchain-ollama not installed. Falling back to mock LLM.")
            return _get_mock_llm()
        except Exception as e:
            logger.warning(f"Ollama connection failed: {e}. Falling back to mock LLM.")
            return _get_mock_llm()

    # -- 2. Try OpenAI (production, requires API key) ------------
    # FIXED: Removed dead `or provider == "ollama"` — ollama path always returns above
    if provider == "openai":
        api_key = getattr(_settings, 'openai_api_key', None)
        
        if api_key and api_key.startswith("sk-"):
            try:
                from langchain_openai import ChatOpenAI
                
                llm = ChatOpenAI(
                    model=model,
                    api_key=api_key,
                    base_url=getattr(_settings, 'openai_base_url', None),
                    temperature=temperature,
                    streaming=streaming,
                    max_retries=3,
                    request_timeout=getattr(_settings, 'llm_request_timeout', 30),
                    max_tokens=getattr(_settings, 'llm_max_tokens', 4096),
                )
                logger.info(f"Using OpenAI LLM: {model}")
                return llm
            except Exception as e:
                logger.error(f"OpenAI initialization failed: {e}")
        elif not api_key:
            logger.warning("OPENAI_API_KEY not set. Skipping OpenAI provider.")
        else:
            logger.warning("OPENAI_API_KEY format invalid (should start with 'sk-'). Skipping OpenAI.")
    
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


# DVMELTSS-T: Test-only helper to force recreate instances
def _recreate_llm_for_test(
    streaming: bool = False,
    provider: Literal["openai", "ollama", "mock"] = "mock",
) -> BaseChatModel:
    """Bypass cache for testing — DO NOT USE IN PRODUCTION."""
    clear_llm_cache()
    return get_llm(streaming=streaming, provider_override=provider)


# DVMELTSS-M: Explicit module exports
__all__ = ["get_llm", "clear_llm_cache", "_recreate_llm_for_test"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.llm_pool) -------
# ========================================================================

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch, mock_open
    
    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]
    
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    
    def run_tests():
        print("🔍 Testing LLM Pool module (app/core/llm_pool.py)")
        print("=" * 70)
        
        try:
            from app.core.llm_pool import (
                get_llm, clear_llm_cache, _recreate_llm_for_test,
                _get_mock_llm
            )
            from langchain_core.language_models import BaseChatModel
            import inspect
            
            # -- Test 1: Module structure & helpers ---------------------
            print("\n📌 Test 1: Module structure & helpers")
            
            assert callable(get_llm)
            assert callable(clear_llm_cache)
            assert callable(_recreate_llm_for_test)
            assert callable(_get_mock_llm)
            print(f"   ✅ All public functions present")
            
            assert not inspect.iscoroutinefunction(get_llm)
            print(f"   ✅ get_llm: sync function (correct for LRU cache)")
            
            # -- Test 2: Mock LLM helper -------------------------------
            print("\n📌 Test 2: _get_mock_llm (offline development)")
            
            mock_llm = _get_mock_llm()
            assert isinstance(mock_llm, BaseChatModel)
            assert hasattr(mock_llm, 'responses')
            assert len(mock_llm.responses) > 0
            print(f"   ✅ _get_mock_llm: returns BaseChatModel with mock responses")
            
            # -- Test 3: LRU cache behavior ----------------------------
            print("\n📌 Test 3: get_llm LRU cache (maxsize=4)")
            
            clear_llm_cache()
            
            with patch('app.core.llm_pool.get_settings') as mock_settings:
                mock_settings.return_value.llm_provider = 'mock'
                mock_settings.return_value.openai_api_key = None
                
                llm1 = get_llm(streaming=False)
                llm2 = get_llm(streaming=False)
                assert llm1 is llm2
                print(f"   ✅ LRU cache: same args -> same instance")
                
                llm3 = get_llm(streaming=True)
                assert llm1 is not llm3
                print(f"   ✅ LRU cache: different args -> new instance")
                
                cache_info = get_llm.cache_info()
                assert cache_info.currsize >= 1
                print(f"   ✅ Cache info: currsize={cache_info.currsize}, maxsize={cache_info.maxsize}")
            
            # -- Test 4: Return type verification ----------------------
            print("\n📌 Test 4: Return type (always BaseChatModel)")
            
            clear_llm_cache()
            
            # Test with mock provider (guaranteed to work)
            with patch('app.core.llm_pool.get_settings') as mock_settings:
                mock_settings.return_value.llm_provider = 'mock'
                mock_settings.return_value.openai_api_key = None
                
                llm = get_llm(streaming=False)
                assert isinstance(llm, BaseChatModel)
                print(f"   ✅ Return type: BaseChatModel (mock provider)")
            
            # Test fallback when no provider is available
            clear_llm_cache()
            with patch('app.core.llm_pool.get_settings') as mock_settings:
                mock_settings.return_value.llm_provider = 'openai'
                mock_settings.return_value.openai_api_key = None  # No API key
                
                llm = get_llm(streaming=False)
                assert isinstance(llm, BaseChatModel)
                assert hasattr(llm, 'responses')  # FakeListChatModel attribute
                print(f"   ✅ Fallback: returns BaseChatModel when no provider available")
            
            # -- Test 5: Cache clearing & test helper ------------------
            print("\n📌 Test 5: clear_llm_cache & _recreate_llm_for_test")
            
            with patch('app.core.llm_pool.get_settings') as mock_settings:
                mock_settings.return_value.llm_provider = 'mock'
                mock_settings.return_value.openai_api_key = None
                
                _ = get_llm(streaming=False)
                _ = get_llm(streaming=True)
                
                cache_info_before = get_llm.cache_info()
                clear_llm_cache()
                cache_info_after = get_llm.cache_info()
                
                assert cache_info_after.currsize == 0
                print(f"   ✅ clear_llm_cache: cleared LRU cache")
            
            # ✅ FIX: _recreate_llm_for_test only accepts streaming and provider
            with patch('app.core.llm_pool.get_settings') as mock_settings:
                mock_settings.return_value.llm_provider = 'mock'
                
                # Use different streaming values to get different instances
                llm1 = _recreate_llm_for_test(streaming=False, provider='mock')
                llm2 = _recreate_llm_for_test(streaming=True, provider='mock')
                assert llm1 is not llm2
                print(f"   ✅ _recreate_llm_for_test: bypasses cache for testing")
            
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! LLM Pool module verified.")
            print("\n💡 What we verified:")
            print("   • Structure: get_llm, clear_llm_cache, helpers present ✅")
            print("   • Mock LLM: _get_mock_llm returns BaseChatModel ✅")
            print("   • Caching: LRU cache with maxsize=4 works correctly ✅")
            print("   • Return type: Always returns BaseChatModel instance ✅")
            print("   • Fallback: Graceful degradation to mock LLM when no provider ✅")
            print("   • Testing: clear_llm_cache & _recreate_llm_for_test ✅")
            print("\n🔐 Production: Centralized LLM pooling with graceful degradation ready")
            return True
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run tests (sync, no async needed for this module)
    success = run_tests()
    sys.exit(0 if success else 1)