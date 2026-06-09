# backend/app/vectorstore/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Vector Store Module
Provides unified interface to ChromaDB + FAISS dual-store architecture:
- ChromaDB: Persistent storage with metadata filtering
- FAISS: In-memory hot cache for fast retrieval
- CachedOpenAIEmbeddings: NumPy-backed embedding cache with PII scrubbing
Public API:
from app.vectorstore import VectorStoreManager, ChromaVectorStore, FAISSVectorStore
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    "VectorStoreManager",
    "ChromaVectorStore",
    "FAISSVectorStore",
    "CachedOpenAIEmbeddings",
    "EMBEDDING_DIM",
    "get_vectorstore_metadata",  # ✅ NEW
]

# ASCALE-S: Module metadata
__version__ = "2.1.0"
__description__ = "DocuMind AI Dual-Store Vector Database Layer"
__stores__ = "ChromaDB (persistent) + FAISS (in-memory cache)"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "VectorStoreManager": (".store_manager", "VectorStoreManager"),
    "ChromaVectorStore": (".chroma_store", "ChromaVectorStore"),
    "FAISSVectorStore": (".faiss_store", "FAISSVectorStore"),
    "CachedOpenAIEmbeddings": (".embeddings", "CachedOpenAIEmbeddings"),
    "EMBEDDING_DIM": (".store_manager", "EMBEDDING_DIM"),
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

    if name == "get_vectorstore_metadata":
        from .store_manager import get_vectorstore_metadata

        return get_vectorstore_metadata

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


# DVMELTSS-T: Test hook — reset singletons for isolated test runs
def _reset_caches_for_tests() -> None:
    """
    Reset internal caches & singletons for clean pytest runs.

    ✅ FIXED: Actually resets all module-level caches (not just invalidate_caches).
    """
    import sys
    import importlib

    # Reset ChromaDB client cache
    try:
        from . import chroma_store

        if hasattr(chroma_store, "_chroma_clients"):
            chroma_store._chroma_clients.clear()
    except ImportError:
        pass

    # Reset FAISSVectorStore instance cache if any
    try:
        from . import faiss_store
        # No module-level singletons to reset
    except ImportError:
        pass

    # Reset CachedOpenAIEmbeddings cache files (optional)
    try:
        from . import embeddings
        # Cache files are on disk — tests should use temp dirs
    except ImportError:
        pass

    # Reset VectorStoreManager singleton if exists
    try:
        from . import store_manager
        # No module-level singleton
    except ImportError:
        pass

    # Invalidate import cache (secondary effect)
    importlib.invalidate_caches()


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
        f"VectorStore module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_vectorstore_metadata() -> dict[str, Any]:
    """Return vectorstore module metadata for monitoring/debugging."""
    from .store_manager import get_vectorstore_metadata as _get_meta

    return _get_meta()
