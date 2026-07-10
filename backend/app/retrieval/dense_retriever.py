
from __future__ import annotations
import asyncio
import logging
import sys
from typing import Final, Optional
from dataclasses import dataclass

# DVMELTSS-M: Import centralized utilities with safe fallbacks
try:
    from app.core.retrieval_utils import (
        safe_vector_search,
        validate_top_k,
        generate_retrieval_correlation_id,
    )
except ImportError:
    # Fallback stubs for graceful degradation
    async def safe_vector_search(search_fn, query_embedding, k, filter_dict, timeout, correlation_id):
        # Simple fallback: call search_fn directly with basic error handling
        try:
            return await asyncio.wait_for(
                search_fn(query_embedding=query_embedding, k=k, filter_dict=filter_dict),
                timeout=timeout,
            )
        except Exception as e:
            logging.warning(f"[{correlation_id}] Fallback vector search failed: {e}")
            return []

    def validate_top_k(k: int, max_k: int = 100) -> int:
        return max(1, min(k, max_k))

    def generate_retrieval_correlation_id(prefix: str = "retrieval") -> str:
        import time
        import secrets

        return f"{prefix}_{int(time.time())}_{secrets.token_hex(4)}"

    logging.warning("⚠️ retrieval_utils imports failed — using fallback implementations")

from app.config import get_settings
from app.vectorstore.store_manager import VectorStoreManager

logger = logging.getLogger(__name__)

# DVMELTSS-S: Default configuration
_DEFAULT_TOP_K: Final = 20
_MAX_TOP_K: Final = 100
_SEARCH_TIMEOUT: Final = 15.0
_MAX_EMBEDDING_DIM: Final = 4096  # ✅ NEW: Memory safety guard


@dataclass(frozen=True)
class DenseRetrievalResult:
    """Immutable result from dense vector retrieval."""

    chunk_id: str
    score: float
    metadata: dict[str, any]
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, any]:
        return {
            "id": self.chunk_id,
            "score": round(self.score, 4),
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
        }


class DenseRetriever:
    """
    Dense vector retrieval using ChromaDB/FAISS embeddings.

    Features (DVMELTSS-V, BATMAN-A):
    - Async-safe search with timeout guards
    - Workspace-scoped filtering
    - Memory-safe batch processing
    - Correlation ID tracing for distributed debugging
    """

    def __init__(self, workspace_id: str = "default"):
        self.workspace_id = workspace_id
        self.settings = get_settings()
        # This ensures blocking init happens before any async calls
        try:
            self._store_manager = VectorStoreManager(workspace_id=workspace_id)
        except Exception as e:
            logger.error(f"Failed to initialize VectorStoreManager for workspace {workspace_id}: {e}")
            self._store_manager = None
        logger.info(f"DenseRetriever initialized: workspace={workspace_id}")

    def _get_store(self) -> Optional[VectorStoreManager]:
        """Get vector store manager — returns None if init failed."""
        return self._store_manager

    async def search_async(
        self,
        query_embedding: list[float],
        k: int = _DEFAULT_TOP_K,
        filter_dict: Optional[dict[str, any]] = None,
        correlation_id: Optional[str] = None,
    ) -> list[DenseRetrievalResult]:
        """
        Async: Search for similar documents using dense embeddings.
        ✅ FIXED: Added embedding dimension validation + memory guard.
        """
        corr_id = correlation_id or generate_retrieval_correlation_id("dense")

        # DVMELTSS-V: Validate top_k
        k = validate_top_k(k, max_k=_MAX_TOP_K)

        if not query_embedding:
            logger.warning(f"[{corr_id}] Empty query embedding")
            return []

        if len(query_embedding) > _MAX_EMBEDDING_DIM:
            logger.error(f"[{corr_id}] Query embedding too large: {len(query_embedding)} > {_MAX_EMBEDDING_DIM}")
            return []

        expected_dim = getattr(self.settings, "embedding_dimension", None)
        if expected_dim and len(query_embedding) != expected_dim:
            logger.warning(
                f"[{corr_id}] Embedding dimension mismatch: got {len(query_embedding)}, expected {expected_dim}"
            )
            # Continue anyway — some models support variable dims

        store = self._get_store()
        if not store:
            logger.error(f"[{corr_id}] VectorStoreManager not available")
            return []

        try:
            # Use centralized safe search with timeout
            raw_results = await safe_vector_search(
                search_fn=store.dense_search,
                query_embedding=query_embedding,
                k=k,
                filter_dict=filter_dict,
                timeout=_SEARCH_TIMEOUT,
                correlation_id=corr_id,
            )

            # Convert to typed results with safe dict access
            results = [
                DenseRetrievalResult(
                    chunk_id=r.get("chunk_id") or r.get("id", "unknown"),
                    score=float(r.get("score", 0.0)),
                    metadata=r.get("metadata", {}) or {},
                    correlation_id=corr_id,
                )
                for r in raw_results
                if r and (r.get("chunk_id") or r.get("id"))
            ]

            logger.debug(f"[{corr_id}] Dense retrieval: {len(results)} results")
            return results

        except Exception as e:
            # ✅ Distinguish retriable vs non-retriable errors
            error_type = type(e).__name__
            if error_type in ("TimeoutError", "ConnectionError", "OSError"):
                logger.warning(f"[{corr_id}] Transient vector store error ({error_type}) — retryable")
            else:
                logger.error(f"[{corr_id}] Dense retrieval failed: {error_type}: {e}")
            return []

    def search(
        self,
        query_embedding: list[float],
        k: int = _DEFAULT_TOP_K,
        filter_dict: Optional[dict[str, any]] = None,
        correlation_id: Optional[str] = None,
    ) -> list[DenseRetrievalResult]:
        """
        Sync wrapper — use search_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ DenseRetriever.search() called from async context — "
                "use search_async() instead. Returning empty results."
            )
            return []
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(self.search_async(query_embedding, k, filter_dict, correlation_id))


# DVMELTSS-M: Explicit module exports
__all__ = ["DenseRetriever", "DenseRetrievalResult"]
# Local smoke test entry point. Run: python -m

