# backend/app/dependencies.py
# DVMELTSS-FIX: M - Modular, A - Async-safe, S - Scalability, D - Dependencies
# ASCALE-FIX: S - Separation, C - Coupling, E - Error handling
# ADDED: Production-ready central dependency providers for OCR, vector store, and RAG chain

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from app.config import get_settings
from app.ocr.pipeline import get_ocr_pipeline
from app.rag.chain import AdvancedRAGChain
from app.vectorstore.store_manager import VectorStoreManager

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_store_manager() -> VectorStoreManager:
    """Return a singleton VectorStoreManager instance."""
    logger.info("Initializing VectorStoreManager singleton")
    return VectorStoreManager()


@lru_cache(maxsize=1)
def get_rag_chain() -> AdvancedRAGChain:
    """Return a singleton AdvancedRAGChain instance."""
    settings = get_settings()
    logger.info("Initializing AdvancedRAGChain singleton")
    store_manager = get_store_manager()
    return AdvancedRAGChain(store_manager=store_manager, use_gpu=settings.ocr_use_gpu)


async def get_async_store_manager() -> VectorStoreManager:
    """Async-compatible getter for the singleton vector store manager."""
    return await asyncio.to_thread(get_store_manager)


async def get_async_rag_chain() -> AdvancedRAGChain:
    """Async-compatible getter for the singleton RAG chain."""
    return await asyncio.to_thread(get_rag_chain)


def get_component_health() -> dict[str, bool]:
    """Return a simple readiness indicator for core components."""
    health: dict[str, bool] = {
        "ocr": False,
        "vectorstore": False,
        "rag_chain": False,
    }

    try:
        ocr = get_ocr_pipeline()
        health["ocr"] = ocr is not None
    except Exception as exc:
        logger.warning("OCR health check failed during dependency health assessment: %s", exc)

    try:
        store = get_store_manager()
        health["vectorstore"] = getattr(store, "_initialized", True)
    except Exception as exc:
        logger.warning(
            "VectorStoreManager health check failed during dependency health assessment: %s",
            exc,
        )

    try:
        rag_chain = get_rag_chain()
        health["rag_chain"] = rag_chain is not None
    except Exception as exc:
        logger.warning("RAG chain health check failed during dependency health assessment: %s", exc)

    return health


def reset_dependency_cache() -> None:
    """Clear cached dependency singletons for testing or restart scenarios."""
    get_store_manager.cache_clear()
    get_rag_chain.cache_clear()


__all__ = [
    "get_store_manager",
    "get_rag_chain",
    "get_async_store_manager",
    "get_async_rag_chain",
    "get_component_health",
    "reset_dependency_cache",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
