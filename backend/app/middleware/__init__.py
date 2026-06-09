# backend/app/rate_limiter/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Rate Limiting Module

Provides Redis-backed sliding window rate limiting for:
- Multi-tenant workspace isolation
- Per-endpoint group quotas (query, ingest, domains)
- Async-safe FastAPI middleware integration
- Fail-safe behavior (configurable fail-closed/open)

Public API:
    from app.rate_limiter import RateLimiter, RateLimitResult, rate_limit_middleware
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    "RateLimiter",
    "RateLimitResult",
    "rate_limit_middleware",
    "DEFAULT_RATE_LIMITS",
    "get_rate_limiter_metadata",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "1.1.0"
__description__ = "DocuMind AI Async Rate Limiting with Redis Sliding Window"
__supported_groups__ = "query, ingest, domains, default"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Core classes
    "RateLimiter": (".rate_limiter", "RateLimiter"),
    "RateLimitResult": (".rate_limiter", "RateLimitResult"),
    # Middleware factory
    "rate_limit_middleware": (".rate_limiter", "rate_limit_middleware"),
    # Constants (note: internal name is _DEFAULT_RATE_LIMITS)
    "DEFAULT_RATE_LIMITS": (".rate_limiter", "_DEFAULT_RATE_LIMITS"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Dynamically resolve imports only when accessed.
    ✅ FIXED: Direct return + explicit error handling.

    Prevents circular imports between rate_limiter ↔ auth ↔ api modules.
    Enables pytest to collect tests without initializing Redis connections.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    if name == "get_rate_limiter_metadata":
        from .rate_limiter import get_rate_limiter_metadata

        return get_rate_limiter_metadata

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


def _reset_caches_for_tests() -> None:
    """Reset internal caches & singletons for clean pytest runs."""
    import importlib
    import sys

    # Invalidate import caches
    try:
        importlib.invalidate_caches()
    except Exception:
        pass

    # ✅ FIXED: Reset module-level singletons if loaded
    try:
        from . import rate_limiter

        # Clear any cached Redis connections or script SHAs
        if hasattr(rate_limiter.RateLimiter, "_redis_pool"):
            rate_limiter.RateLimiter._redis_pool = None
    except ImportError:
        pass


# DVMELTSS-L: Module initialization logging for observability
__init_logged: bool = False


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return

    import logging

    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Rate limiter module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_rate_limiter_metadata() -> dict[str, Any]:
    """Return rate limiter module metadata for debugging."""
    from .rate_limiter import get_rate_limiter_metadata as _get_meta

    return {
        "version": __version__,
        "description": __description__,
        "supported_groups": __supported_groups__,
        "rate_limiter": _get_meta(),
        "features": [
            "sliding_window",
            "redis_backed",
            "async_safe",
            "workspace_isolation",
            "fail_open_configurable",
            "lua_atomicity",
        ],
    }
