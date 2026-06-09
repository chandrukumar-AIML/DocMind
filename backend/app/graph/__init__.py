# backend/app/graph/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Knowledge Graph Module

Provides graph-based retrieval and entity extraction:
- Neo4j store interface with async query execution
- GPT-4o powered entity/relationship extraction
- Hybrid GraphRAG retrieval combining Neo4j + ChromaDB
- Cypher generation with injection prevention

Public API:
    from app.graph import Neo4jStore, GraphExtractor, GraphRAGRetriever
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Neo4j Store
    "Neo4jStore",
    "get_neo4j_store",
    "Neo4jQueryResult",
    # Graph Extraction
    "GraphExtractor",
    "ExtractedEntity",
    "ExtractedRelationship",
    "ExtractionResult",
    # Cypher Retrieval
    "CypherRetriever",
    # GraphRAG
    "GraphRAGRetriever",
    "GraphRAGResult",
    # Metadata helpers
    "get_graph_metadata",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "1.1.0"
__description__ = "DocuMind AI Knowledge Graph & GraphRAG Pipeline"
__supported_entity_types__ = "Person, Organization, Contract, Clause, Date, Location, Concept, Amount, Document"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Neo4j Store
    "Neo4jStore": (".neo4j_store", "Neo4jStore"),
    "get_neo4j_store": (".neo4j_store", "get_neo4j_store"),
    "Neo4jQueryResult": (".neo4j_store", "Neo4jQueryResult"),
    # Graph Extraction
    "GraphExtractor": (".graph_extractor", "GraphExtractor"),
    "ExtractedEntity": (".graph_extractor", "ExtractedEntity"),
    "ExtractedRelationship": (".graph_extractor", "ExtractedRelationship"),
    "ExtractionResult": (".graph_extractor", "ExtractionResult"),
    # Cypher Retrieval
    "CypherRetriever": (".cypher_retriever", "CypherRetriever"),
    # GraphRAG
    "GraphRAGRetriever": (".graph_rag", "GraphRAGRetriever"),
    "GraphRAGResult": (".graph_rag", "GraphRAGResult"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Dynamically resolve imports only when accessed.
    ✅ FIXED: Direct return + explicit error handling.

    Prevents circular imports between graph ↔ extraction ↔ agent modules.
    Enables pytest to collect tests without initializing Neo4j/OpenAI clients.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    if name == "get_graph_metadata":
        from .neo4j_store import get_neo4j_metadata

        return get_neo4j_metadata

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
    for mod_name in [
        ".neo4j_store",
        ".graph_extractor",
        ".cypher_retriever",
        ".graph_rag",
    ]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    # ✅ FIXED: Use getattr to safely access lazy-imported function
    if hasattr(sys.modules[__name__], "get_neo4j_store"):
        try:
            # Import locally to get the actual function with cache_clear method
            from .neo4j_store import get_neo4j_store

            get_neo4j_store.cache_clear()
        except ImportError:
            pass  # Module not loaded yet — nothing to clear


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
        f"Graph module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_graph_metadata() -> dict[str, Any]:
    """Return graph module metadata for monitoring/debugging."""
    from .neo4j_store import get_neo4j_metadata as _get_meta

    return _get_meta()
