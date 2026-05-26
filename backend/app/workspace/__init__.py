# backend/app/workspace/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Workspace Management Module
Provides:
- Workspace provisioning and teardown for multi-tenant isolation
- Request-scoped workspace context injection
- Storage resource management (ChromaDB, Neo4j, BM25, PostgreSQL)
Public API:
from app.workspace import WorkspaceManager, WorkspaceContext, workspace_context
"""
from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Manager
    "WorkspaceManager", "WorkspaceResources",
    # Context
    "WorkspaceContext", "workspace_context",
    # Metadata helpers
    "get_workspace_metadata",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "1.1.0"
__description__ = "DocuMind AI Multi-Tenant Workspace Management"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Manager
    "WorkspaceManager": (".manager", "WorkspaceManager"),
    "WorkspaceResources": (".manager", "WorkspaceResources"),
    # Context
    "WorkspaceContext": (".context", "WorkspaceContext"),
    "workspace_context": (".context", "workspace_context"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Lazy imports to prevent circular dependencies.
    ✅ FIXED: Direct return + explicit error handling.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_path, package=__name__)
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(
                f"Failed to lazy-import '{name}' from '{module_path}': {e}"
            ) from e
    
    if name == "get_workspace_metadata":
        from .manager import get_workspace_metadata
        return get_workspace_metadata
    
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
    for mod_name in [".manager", ".context"]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass
    
    # ✅ FIXED: Reset module-level singletons if loaded
    try:
        from . import manager
        if hasattr(manager, "_workspace_cache"):
            manager._workspace_cache.clear()
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
        f"Workspace module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_workspace_metadata() -> dict[str, Any]:
    """Return workspace module metadata for debugging."""
    from .manager import get_workspace_metadata as _get_manager_meta
    from .context import get_workspace_context_metadata as _get_context_meta
    
    return {
        "version": __version__,
        "description": __description__,
        "components": {
            "manager": _get_manager_meta(),
            "context": _get_context_meta(),
        },
        "features": [
            "multi_tenant_isolation",
            "resource_provisioning",
            "request_scoped_context",
            "async_safe",
            "graceful_degradation",
        ],
    }
