# backend/app/retrieval/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: __getattr__ returns values directly (not via unreliable locals())
# ✅ FIXED: _reset_caches_for_tests() actually resets module caches or is documented no-op
# ✅ FIXED: Lazy import error handling with clear messages
# ✅ FIXED: Idempotent module init logging + debug level
# ✅ FIXED: Added __dir__() for IDE/tab-completion support

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Retrievers
    "DenseRetriever",
    "BM25Retriever",
    "HybridRetriever",
    # Utilities
    "reciprocal_rank_fusion",
    "hybrid_score",
    # Benchmarking
    "RetrievalBenchmark",
    # Profiles
    "RETRIEVAL_PROFILES",
]

# ASCALE-S: Module metadata
__version__ = "2.1.0"
__description__ = "DocuMind AI Hybrid Retrieval Pipeline"
__supported_backends__ = "chroma, faiss, bm25, hybrid"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # (module_path, attribute_name)
    "DenseRetriever": (".dense_retriever", "DenseRetriever"),
    "BM25Retriever": (".bm25_retriever", "BM25Retriever"),
    "HybridRetriever": (".hybrid_retriever", "HybridRetriever"),
    "RetrievalBenchmark": (".benchmark", "RetrievalBenchmark"),
    "RETRIEVAL_PROFILES": (".profiles", "RETRIEVAL_PROFILES"),
}

_UTIL_IMPORTS: dict[str, tuple[str, str]] = {
    "reciprocal_rank_fusion": ("app.core.retrieval_utils", "reciprocal_rank_fusion"),
    "hybrid_score": ("app.core.retrieval_utils", "hybrid_score"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Lazy imports to prevent circular dependencies.
    ✅ FIXED: Direct return + explicit error handling.
    """
    # Core retrievers
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__)
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    # Utilities (centralized)
    if name in _UTIL_IMPORTS:
        module_path, attr_name = _UTIL_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path)
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import utility '{name}' from '{module_path}': {e}") from e

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


# -- Test utilities ------------------------------------------------------
def _reset_caches_for_tests() -> None:
    """
    Reset internal caches for clean pytest runs.

    ✅ FIXED: Actually resets module-level singletons if they expose clear().
    If modules don't support reset, this is a documented no-op.
    """
    import sys
    import importlib

    # Try to reset caches in loaded retrieval modules
    for mod_name in [
        "app.retrieval.dense_retriever",
        "app.retrieval.bm25_retriever",
        "app.retrieval.hybrid_retriever",
        "app.retrieval.benchmark",
        "app.retrieval.rrf_fusion",
    ]:
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
            # Call clear/reset methods if they exist
            for obj_name in dir(mod):
                obj = getattr(mod, obj_name)
                if hasattr(obj, "clear_index") and callable(obj.clear_index):
                    try:
                        obj.clear_index()
                    except Exception:
                        pass  # Safe no-op for tests

    # Invalidate import cache (secondary effect)
    importlib.invalidate_caches()


# -- Module init logging (idempotent) ------------------------------------
__init_logged: bool = False


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return

    import logging

    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Retrieval module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


_log_module_init()
