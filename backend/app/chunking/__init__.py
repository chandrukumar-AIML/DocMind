
"""
DocuMind AI - Document Chunking Module

Provides parent-child chunking strategies for RAG pipelines:
- Parent chunks: Large context (1000-2000 chars) for semantic understanding
- Child chunks: Small retrieval units (200-400 chars) for precise matching
- Async streaming support for memory-efficient ingestion

Public API:
    from app.chunking import ParentChildChunker, ChunkMetadata, get_chunker

Usage:
    chunker = get_chunker()
    async for child, parent in chunker.chunk_enriched_document(doc, "file.pdf"):
        await vector_store.aadd_documents([child])
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

# ✅ Type-checker only imports — prevents runtime circular deps
if TYPE_CHECKING:
    from .parent_child import ChunkMetadata, ParentChildChunker

# ========================================================================
# -- PUBLIC API SURFACE (DVMELTSS-M: Explicit exports) -----------------
# ========================================================================

__all__ = [
    "ParentChildChunker",
    "ChunkMetadata",
    "get_chunker",
    "normalize_chunking_tags",
    "get_chunker_metadata",
    "_reset_chunker_instance_for_tests",  # Test-only hook
]

# Module metadata for observability
__version__ = "1.0.0"
__chunking_strategy__ = "Parent-Child + Async Streaming + Deterministic IDs"


# ========================================================================
# -- SINGLETON MANAGEMENT (BATMAN-A: Lazy init) -----------------------
# ========================================================================

_chunker_instance: "ParentChildChunker | None" = None
_init_logged: bool = False


def get_chunker() -> "ParentChildChunker":
    """
    Get or create the singleton ParentChildChunker instance.
    DVMELTSS-M: Lazy initialization — no heavy imports until first use.
    BATMAN-A: Safe for async FastAPI startup.

    Returns:
        ParentChildChunker: Configured chunker with settings from app.config.
    """
    global _chunker_instance
    if _chunker_instance is None:
        # ✅ Lazy import — only when actually needed
        from .parent_child import ParentChildChunker

        _chunker_instance = ParentChildChunker()
        _log_module_init()
    return _chunker_instance


# ========================================================================
# -- CONVENIENCE FUNCTIONS (DVMELTSS-M: Re-export utilities) ---------
# ========================================================================


def normalize_chunking_tags(tags: list[str] | None) -> list[str]:
    """Alias for app.core.validators.normalize_tags — for convenient imports."""
    from app.core.validators import normalize_tags as _normalize

    return _normalize(tags or [])


def get_chunker_metadata() -> dict[str, Any]:
    """
    Return chunker module metadata for monitoring/debugging.
    ✅ Single source of truth — no lazy import confusion.
    """
    from .parent_child import get_chunker_metadata as _get_meta

    return _get_meta()


# ========================================================================
# -- LAZY IMPORTS FOR TYPE CLASSES (DVMELTSS-T: Avoid circular deps) -
# ========================================================================

_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    "ParentChildChunker": (".parent_child", "ParentChildChunker"),
    "ChunkMetadata": (".parent_child", "ChunkMetadata"),
}


def __getattr__(name: str) -> Any:
    """
    Dynamically resolve type/class imports only when accessed.
    ✅ FIXED: Only handles items NOT already defined above.

    Prevents circular imports between chunking ↔ ocr ↔ vectorstore modules.
    Enables pytest to collect tests without initializing heavy dependencies.
    """
    # ✅ Only handle lazy imports for classes not defined in this file
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    # ✅ Functions already defined above — raise clear error if accessed wrongly
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Enable IDE/tab-completion for all public names."""
    return sorted(set(__all__))


# ========================================================================
# -- TEST HOOKS (DVMELTSS-T: Isolated test runs) ----------------------
# ========================================================================


def _reset_chunker_instance_for_tests() -> None:
    """
    Reset the global chunker instance — for pytest fixtures only.

    ✅ FIXED: Resets THIS module's singleton + clears any lru_cache in parent_child.

    Usage in conftest.py:
        @pytest.fixture(autouse=True)
        def reset_chunker():
            from app.chunking import _reset_chunker_instance_for_tests
            _reset_chunker_instance_for_tests()
            yield
    """
    global _chunker_instance
    _chunker_instance = None

    # ✅ Clear any lru_cache in parent_child module for clean test state
    try:
        from . import parent_child

        for name in dir(parent_child):
            obj = getattr(parent_child, name)
            if hasattr(obj, "cache_clear") and callable(obj.cache_clear):
                obj.cache_clear()
    except ImportError:
        pass

    logging.getLogger(__name__).debug("Chunker instance reset for tests")


# ========================================================================
# -- LOGGING (DVMELTSS-L: Idempotent module init logging) -----------
# ========================================================================


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global _init_logged
    if _init_logged:
        return

    logger = logging.getLogger(__name__)
    logger.debug(f"Chunking module loaded | version={__version__} | strategy={__chunking_strategy__}")
    _init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()
