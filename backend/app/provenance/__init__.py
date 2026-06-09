# backend/app/provenance/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Provenance & Citation Tracking Module
Provides:
- SQLAlchemy models for answer/citation storage
- Async PostgreSQL store with connection pooling
- PDF highlight computation with confidence-based coloring
- Text offset finding for react-pdf text layer integration

Public API:
from app.provenance import ProvenanceStore, compute_highlight_color, find_text_offset
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Models
    "Base",
    "Answer",
    "Citation",
    "DocumentStore",
    "HighlightColor",
    # Store
    "ProvenanceStore",
    "init_db",
    "get_engine",
    "get_session_factory",
    # Highlight utilities
    "compute_highlight_color",
    "find_text_offset",
    "compute_citation_offsets",
    # Metadata helpers
    "get_provenance_metadata",
]

# ASCALE-S: Module metadata
__version__ = "1.1.0"
__description__ = "DocuMind AI Answer Provenance & Citation Tracking"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Models
    "Base": (".models", "Base"),
    "Answer": (".models", "Answer"),
    "Citation": (".models", "Citation"),
    "DocumentStore": (".models", "DocumentStore"),
    "HighlightColor": (".models", "HighlightColor"),
    # Store
    "ProvenanceStore": (".store", "ProvenanceStore"),
    "init_db": (".store", "init_db"),
    "get_engine": (".store", "get_engine"),
    "get_session_factory": (".store", "get_session_factory"),
    # Highlight utilities
    "compute_highlight_color": (".highlight", "compute_highlight_color"),
    "find_text_offset": (".highlight", "find_text_offset"),
    "compute_citation_offsets": (".highlight", "compute_citation_offsets"),
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

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    if name == "get_provenance_metadata":
        from .store import get_provenance_metadata

        return get_provenance_metadata

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


def _reset_caches_for_tests() -> None:
    """Reset internal caches for clean pytest runs."""
    import importlib
    import sys

    # Invalidate import caches
    for mod_name in [".models", ".store", ".highlight"]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    # ✅ FIXED: Reset module-level singletons in store.py
    try:
        from . import store

        if hasattr(store, "_engines"):
            store._engines.clear()
        if hasattr(store, "_session_factories"):
            store._session_factories.clear()
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
        f"Provenance module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_provenance_metadata() -> dict[str, Any]:
    """Return provenance module metadata for monitoring/debugging."""
    from .store import get_provenance_metadata as _get_meta

    return _get_meta()
