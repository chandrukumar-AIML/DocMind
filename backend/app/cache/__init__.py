# backend/app/cache/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Logging/Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - Async-safe initialization
# ✅ FINAL FIX: Correct singleton management + clean lazy imports + test-safe reset

"""
DocuMind AI - Query Cache Module

Redis-backed two-level caching for RAG pipelines:
- Level 1: Embedding vector cache (stable, long TTL)
- Level 2: Full query result cache (time-sensitive, short TTL)
- Workspace-scoped invalidation for document updates

Public API:
    from app.cache import QueryCache, CacheStats, get_cache

Usage:
    cache = await get_cache()
    if result := await cache.get_result(ws_id, question):
        return result  # Cache hit!
    # ... generate result ...
    await cache.set_result(ws_id, question, result)
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Optional

# ✅ Type-checker only imports — prevents runtime circular deps
if TYPE_CHECKING:
    from .query_cache import CacheStats, QueryCache

# ========================================================================
# -- PUBLIC API SURFACE (DVMELTSS-M: Explicit exports) -----------------
# ========================================================================

__all__ = [
    "QueryCache",
    "CacheStats", 
    "get_cache",
    "init_cache",
    "invalidate_workspace_cache",
    "get_cache_metadata",
    "_reset_cache_instance_for_tests",  # Test-only hook
]

# Module metadata for observability
__version__ = "1.2.0"
__cache_provider__ = "Redis Asyncio + SHA256 keys + Workspace index"
__cache_strategy__ = "Two-level (embed/result) + O(1) invalidation + Circuit Breaker"


# ========================================================================
# -- SINGLETON MANAGEMENT (BATMAN-A: Async-safe lazy init) -----------
# ========================================================================

_cache_instance: Optional["QueryCache"] = None
_cache_init_lock: Optional[asyncio.Lock] = None
_init_logged: bool = False


async def get_cache() -> "QueryCache":
    """
    Get or create the singleton QueryCache instance.
    DVMELTSS-M: Lazy async initialization — no Redis connection until first use.
    BATMAN-A: Safe for async FastAPI startup with proper locking.
    
    Returns:
        QueryCache: Configured async Redis-backed cache client.
    """
    global _cache_instance, _cache_init_lock
    
    # Fast path: already initialized
    if _cache_instance is not None:
        return _cache_instance
    
    # Create lock if needed
    if _cache_init_lock is None:
        _cache_init_lock = asyncio.Lock()
    
    # Double-checked locking for async safety
    async with _cache_init_lock:
        if _cache_instance is None:
            # ✅ Lazy import — only when actually needed
            from .query_cache import QueryCache
            _cache_instance = QueryCache()
            _log_module_init()
    
    return _cache_instance


async def init_cache() -> "QueryCache":
    """
    Initialize the cache singleton — explicit init hook for lifespan management.
    Backward-compatible alias for get_cache().
    
    Returns:
        QueryCache: The initialized cache instance.
    """
    # ✅ FIXED: Just call get_cache() and return the global instance
    await get_cache()
    return _cache_instance  # type: ignore[return-value]


async def invalidate_workspace_cache(workspace_id: str) -> int:
    """
    Convenience function to invalidate all cached results for a workspace.
    Called after document ingest/delete operations.
    
    Args:
        workspace_id: Target workspace to invalidate
        
    Returns:
        int: Number of cache entries deleted
    """
    cache = await get_cache()
    return await cache.invalidate_workspace(workspace_id)


def get_cache_metadata() -> dict[str, Any]:
    """
    Return cache module metadata for monitoring/debugging.
    ✅ Single source of truth — no lazy import confusion.
    """
    from .query_cache import get_cache_metadata as _get_meta
    return _get_meta()


# ========================================================================
# -- LAZY IMPORTS FOR TYPE CLASSES (DVMELTSS-T: Avoid circular deps) -
# ========================================================================

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "QueryCache": (".query_cache", "QueryCache"),
    "CacheStats": (".query_cache", "CacheStats"),
}


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve type/class imports only when accessed.
    ✅ FIXED: Only handles items NOT already defined above.
    
    Prevents circular imports between cache ↔ agent ↔ provenance modules.
    Enables pytest to collect tests without initializing Redis.
    """
    # ✅ Only handle lazy imports for classes not defined in this file
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_path, package=__name__.rpartition('.')[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(
                f"Failed to lazy-import '{name}' from '{module_path}': {e}"
            ) from e
    
    # ✅ Functions already defined above — raise clear error if accessed wrongly
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Enable IDE/tab-completion for all public names."""
    return sorted(set(__all__))


# ========================================================================
# -- TEST HOOKS (DVMELTSS-T: Isolated test runs) ----------------------
# ========================================================================

async def _reset_cache_instance_for_tests() -> None:
    """
    Reset the global cache instance — for pytest fixtures only.
    
    ✅ FIXED: Resets variables in THIS module (__init__.py), not query_cache.py.
    
    Usage in conftest.py:
        @pytest_asyncio.fixture(autouse=True)
        async def reset_cache():
            from app.cache import _reset_cache_instance_for_tests
            await _reset_cache_instance_for_tests()
            yield
    """
    global _cache_instance, _cache_init_lock, _init_logged
    
    # ✅ Reset THIS module's state
    _cache_instance = None
    _cache_init_lock = None
    _init_logged = False  # Allow re-logging on re-init
    
    # Optional: Close existing connection if open
    if hasattr(_cache_instance, "close") and _cache_instance is not None:
        try:
            await _cache_instance.close()
        except Exception:
            pass  # Ignore cleanup errors in test reset
    
    logging.getLogger(__name__).debug("QueryCache instance reset for tests")


# ========================================================================
# -- LOGGING (DVMELTSS-L: Idempotent module init logging) -----------
# ========================================================================

def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global _init_logged
    if _init_logged:
        return
    
    logger = logging.getLogger(__name__)
    logger.debug(
        f"Cache module loaded | version={__version__} | provider={__cache_provider__}"
    )
    _init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()