
"""
DocuMind AI - Document Versioning Module
Provides:
- Semantic diff computation between document versions
- Change summarization via LLM
- Version registry with metadata tracking
- Rollback and comparison utilities
Public API:
from app.versioning import DiffEngine, VersionRegistry, compute_document_diff
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Core versioning
    "DiffEngine",
    "VersionRegistry",
    "VersionMetadata",
    # Diff utilities
    "compute_document_diff",
    "summarize_changes",
    # Models
    "DiffResult",
    "VersionComparison",
    # Summarizer
    "generate_change_summary_async",
    "generate_fallback_summary",
    # Metadata helpers
    "get_versioning_metadata",
]

# ASCALE-S: Module metadata
__version__ = "1.0.0"
__description__ = "DocuMind AI Document Versioning & Diff Pipeline"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Core classes
    "DiffEngine": (".registry", "DiffEngine"),
    "VersionRegistry": (".registry", "VersionRegistry"),
    "VersionMetadata": (".models", "VersionMetadata"),
    # Diff utilities
    "compute_document_diff": (".diff_engine", "compute_document_diff"),
    "summarize_changes": (".diff_engine", "summarize_changes"),
    # Models
    "DiffResult": (".models", "DiffResult"),
    "VersionComparison": (".models", "VersionComparison"),
    # Summarizer
    "generate_change_summary_async": (
        ".diff_summarizer",
        "generate_change_summary_async",
    ),
    "generate_fallback_summary": (".diff_summarizer", "generate_fallback_summary"),
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

    if name == "get_versioning_metadata":
        from .registry import get_versioning_metadata

        return get_versioning_metadata

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
    for mod_name in [".diff_engine", ".diff_summarizer", ".models", ".registry"]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    try:
        from . import registry

        if hasattr(registry, "_version_cache"):
            registry._version_cache.clear()
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
        f"Versioning module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


def get_versioning_metadata() -> dict[str, Any]:
    """Return versioning module metadata for debugging."""
    from .registry import get_versioning_metadata as _get_registry_meta
    from .diff_engine import get_diff_engine_metadata as _get_diff_meta
    from .diff_summarizer import get_summarizer_metadata as _get_summarizer_meta
    from .models import get_versioning_models_metadata as _get_models_meta

    return {
        "version": __version__,
        "description": __description__,
        "components": {
            "registry": _get_registry_meta(),
            "diff_engine": _get_diff_meta(),
            "summarizer": _get_summarizer_meta(),
            "models": _get_models_meta(),
        },
        "features": [
            "semantic_diff",
            "llm_summarization",
            "version_tracking",
            "rollback_support",
            "async_safe",
            "graceful_degradation",
        ],
    }
