# ADDED: Production-ready central dependency providers for OCR, vector store, and RAG chain

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

from app.config import get_settings
from app.ocr.pipeline import get_ocr_pipeline
from app.rag.chain import AdvancedRAGChain
from app.agent.agent_chain import AgentRAGChain
from app.vectorstore.store_manager import VectorStoreManager

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_store_manager() -> VectorStoreManager:
    """Return a singleton VectorStoreManager instance (legacy global default — used
    only by health checks; request paths should use get_store_manager_for_workspace)."""
    logger.info("Initializing VectorStoreManager singleton")
    return VectorStoreManager()


# ── Per-workspace vector store resolution ───────────────────────────────────
# Unlike get_store_manager()'s single global instance, request-serving paths (RAG
# chain, agent nodes, graph retriever) must resolve a store scoped to the caller's
# workspace_id — otherwise every workspace shares one Chroma collection/FAISS index.
# Cached per workspace_id since VectorStoreManager construction (embeddings client,
# FAISS load) is not free; no invalidation needed since a workspace's vector store
# location is static, unlike per-workspace LLM config in llm_pool.py.
_workspace_store_cache: dict[str, VectorStoreManager] = {}


def get_store_manager_for_workspace(workspace_id: str) -> VectorStoreManager:
    """Return a cached, workspace-scoped VectorStoreManager."""
    cached = _workspace_store_cache.get(workspace_id)
    if cached is not None:
        return cached
    manager = VectorStoreManager(workspace_id=workspace_id)
    _workspace_store_cache[workspace_id] = manager
    return manager


@lru_cache(maxsize=1)
def get_rag_chain() -> AdvancedRAGChain:
    """Return a singleton AdvancedRAGChain instance."""
    settings = get_settings()
    logger.info("Initializing AdvancedRAGChain singleton")
    store_manager = get_store_manager()
    return AdvancedRAGChain(store_manager=store_manager, use_gpu=settings.ocr_use_gpu)


@lru_cache(maxsize=1)
def get_agent_chain() -> AgentRAGChain:
    """Return a singleton AgentRAGChain instance."""
    logger.info("Initializing AgentRAGChain singleton")
    return AgentRAGChain()


async def get_async_store_manager() -> VectorStoreManager:
    """Async-compatible getter for the singleton vector store manager."""
    return await asyncio.to_thread(get_store_manager)


async def get_async_store_manager_for_workspace(workspace_id: str) -> VectorStoreManager:
    """Async-compatible getter for a workspace-scoped vector store manager."""
    return await asyncio.to_thread(get_store_manager_for_workspace, workspace_id)


async def get_async_rag_chain() -> AdvancedRAGChain:
    """Async-compatible getter for the singleton RAG chain."""
    return await asyncio.to_thread(get_rag_chain)


async def get_async_agent_chain() -> AgentRAGChain:
    """Async-compatible getter for the singleton agent chain."""
    return await asyncio.to_thread(get_agent_chain)


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
    _workspace_store_cache.clear()


__all__ = [
    "get_store_manager",
    "get_store_manager_for_workspace",
    "get_rag_chain",
    "get_async_store_manager",
    "get_async_store_manager_for_workspace",
    "get_async_rag_chain",
    "get_component_health",
    "reset_dependency_cache",
]
# Local smoke test entry point. Run: python -m

