# backend/app/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Logging/Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - Async-safe initialization
"""
DocuMind AI — Multi-Domain Document Intelligence Platform

Architecture:
- Input: PDF/Image/Audio -> OCR/Vision -> Chunking -> Vector/Graph Store
- Query: Natural Language -> Agent (CRAG + Self-RAG) -> Answer + Citations
- Domains: Legal (clauses/risk), Medical (ICD-10/drug), Logistics (invoice/anomaly)
- Observability: LangSmith tracing + MLflow metrics + Prometheus endpoints

Public API:
    from app import __version__, create_app
    from app.dependencies import get_store_manager, get_ocr_pipeline
    from app.config import get_settings

Usage:
    app = create_app()  # FastAPI instance with lifespan management
"""

from __future__ import annotations

# ========================================================================
# MODULE METADATA (DVMELTSS-L: Logging/Metadata for observability)
# ========================================================================
__version__ = "2.0.0-phase-e"
__author__ = "DocuMind AI Team"
__license__ = "Proprietary"
__agent_architecture__ = "LangGraph + CRAG + Self-RAG + HyDE"
__supported_domains__ = ["general", "legal", "medical", "logistics"]


# ========================================================================
# LAZY IMPORTS (DVMELTSS-M: Modular — prevent circular deps at startup)
# ========================================================================
def create_app():
    """
    Factory function for FastAPI application instance.

    Returns:
        FastAPI app with lifespan, middleware, routes, and exception handlers

    Usage:
        from app import create_app
        app = create_app()
    """
    from app.main import create_app as _create_app

    return _create_app()


def get_settings():
    """
    Get application settings singleton (cached).

    Returns:
        Settings instance with env vars loaded

    Usage:
        from app import get_settings
        settings = get_settings()
        api_key = settings.openai_api_key
    """
    from app.config import get_settings as _get_settings

    return _get_settings()


# ========================================================================
# DEPENDENCY GETTERS (DVMELTSS-M: Centralized access to core services)
# ========================================================================
def get_ocr_pipeline():
    """
    Get singleton OCR pipeline (PaddleOCR + Vision fallback).

    Returns:
        OCRPipeline instance with loaded models

    Note:
        Blocking initialization — use get_async_ocr_pipeline() in async contexts
    """
    from app.dependencies import get_ocr_pipeline as _get_ocr

    return _get_ocr()


def get_store_manager():
    """
    Get singleton vector store manager (ChromaDB + FAISS + BM25).

    Returns:
        VectorStoreManager instance with initialized stores
    """
    from app.dependencies import get_store_manager as _get_store

    return _get_store()


def get_rag_chain():
    """
    Get singleton RAG chain (AdvancedRAGChain).

    Returns:
        AdvancedRAGChain instance (call .initialize() for BM25 setup)
    """
    from app.dependencies import get_rag_chain as _get_rag

    return _get_rag()


# ========================================================================
# ASYNC-AWARE GETTERS (for FastAPI lifespan / background tasks)
# ========================================================================
async def get_async_store_manager():
    """Async-compatible store manager getter (non-blocking init)."""
    from app.dependencies import get_async_store_manager as _get_async_store

    return await _get_async_store()


async def get_async_rag_chain():
    """Async-compatible RAG chain getter with BM25 initialization."""
    from app.dependencies import get_async_rag_chain as _get_async_rag

    return await _get_async_rag()


# ========================================================================
# HEALTH CHECK HELPERS (for /health endpoint + Kubernetes probes)
# ========================================================================
def get_component_health() -> dict[str, bool]:
    """
    Get health status of all core components.

    Returns:
        Dict with component names and boolean health status

    Example:
        {"ocr": True, "vectorstore": True, "rag_chain": True}
    """
    from app.dependencies import get_component_health as _get_health

    return _get_health()


# ========================================================================
# DVMELTSS-T: Safe lazy-import fallback for circular import safety
# ========================================================================
def __getattr__(name: str):
    """
    Lazy attribute loading for circular import safety.

    Python 3.7+ compatible. Prevents crashes during:
    - FastAPI startup with complex dependency graphs
    - Unit tests that mock submodules
    - IDE type checking without full import

    Args:
        name: Attribute name being accessed

    Returns:
        Imported attribute or raises AttributeError

    Raises:
        AttributeError: If attribute doesn't exist
    """
    # Allow direct access to metadata without imports
    if name in {"__version__", "__author__", "__license__"}:
        return globals()[name]

    # Lazy-load heavy dependencies only when needed
    lazy_imports = {
        "AgentRAGChain": ("app.agent", "AgentRAGChain"),
        "AgentState": ("app.agent", "AgentState"),
        "OCRPipeline": ("app.ocr.pipeline", "OCRPipeline"),
        "VectorStoreManager": ("app.vectorstore.store_manager", "VectorStoreManager"),
        "AdvancedRAGChain": ("app.rag.chain", "AdvancedRAGChain"),
        "HybridRetriever": ("app.retrieval.hybrid_search", "HybridRetriever"),
        "QueryCache": ("app.cache.query_cache", "QueryCache"),
    }

    if name in lazy_imports:
        module_path, attr_name = lazy_imports[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr_name)

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


# ========================================================================
# EXPORTS (DVMELTSS-M: Explicit public API surface)
# ========================================================================
__all__ = [
    # Metadata
    "__version__",
    "__author__",
    "__license__",
    "__agent_architecture__",
    "__supported_domains__",
    # App factory
    "create_app",
    # Config
    "get_settings",
    # Core dependencies (sync)
    "get_ocr_pipeline",
    "get_store_manager",
    "get_rag_chain",
    # Core dependencies (async)
    "get_async_store_manager",
    "get_async_rag_chain",
    # Health checks
    "get_component_health",
]
