# backend/app/retrieval/hybrid_retriever.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async orchestration
# BATMAN-FIX: A - True async, T - Concurrent execution + timeout guards
# ✅ FIXED: Safe sync wrapper (no deadlock in FastAPI)
# ✅ FIXED: Added timeout protection on concurrent retriever tasks
# ✅ FIXED: Python 3.8 fallback for asyncio.to_thread()
# ✅ FIXED: Fallback scoring for chunks missing from RRF output
# ✅ FIXED: Safe import fallbacks for utility functions

from __future__ import annotations
import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Final, Optional

# FIXED: Removed duplicate 'import logging' and logging.basicConfig() — never configure
# root logger in library code (it pollutes the entire application's log output)
logger = logging.getLogger(__name__)

# DVMELTSS-M: Import centralized utilities with safe fallbacks
try:
    from app.core.retrieval_utils import (
        reciprocal_rank_fusion,
        validate_top_k,
        generate_retrieval_correlation_id,
    )
except ImportError:
    # Fallback stubs for graceful degradation
    def reciprocal_rank_fusion(results: list, k: int = 60, weights: list = None):
        # Simple fallback: average ranks if RRF unavailable
        fused = {}
        for i, result_list in enumerate(results):
            weight = weights[i] if weights and i < len(weights) else 1.0
            for rank, item in enumerate(result_list, start=1):
                chunk_id = item.get("chunk_id") or item.get("id")
                if chunk_id:
                    fused[chunk_id] = fused.get(chunk_id, 0) + weight / (rank + k)
        return fused

    def validate_top_k(k: int, max_k: int = 100) -> int:
        return max(1, min(k, max_k))

    def generate_retrieval_correlation_id(prefix: str = "retrieval") -> str:
        import time
        import secrets

        return f"{prefix}_{int(time.time())}_{secrets.token_hex(4)}"

    logger.warning("⚠️ retrieval_utils imports failed — using fallback implementations")

from .dense_retriever import DenseRetriever, DenseRetrievalResult
from .bm25_retriever import BM25Retriever, BM25RetrievalResult

logger = logging.getLogger(__name__)

# DVMELTSS-S: Hybrid configuration
_DEFAULT_TOP_K: Final = 20
_DEFAULT_ALPHA: Final = 0.5
_RRF_K: Final = 60
_RETRIEVER_TIMEOUT_SECONDS: Final = 30  # ✅ NEW: Per-retriever timeout


@dataclass(frozen=True)
class HybridRetrievalResult:
    """Immutable result from hybrid retrieval."""

    chunk_id: str
    score: float
    dense_score: float
    sparse_score: float
    metadata: dict[str, any]
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, any]:
        return {
            "id": self.chunk_id,
            "score": round(self.score, 4),
            "dense_score": round(self.dense_score, 4),
            "sparse_score": round(self.sparse_score, 4),
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
        }


class HybridRetriever:
    """
    Hybrid retrieval combining dense and sparse methods via RRF.

    Algorithm:
    1. Run dense (vector) and sparse (BM25) searches in parallel
    2. Apply Reciprocal Rank Fusion to merge rankings
    3. Return top-k results with combined scores

    Features (DVMELTSS-V, BATMAN-A):
    - Concurrent execution with timeout guards
    - Configurable weighting via alpha parameter
    - Python 3.8+ compatible async/sync interface
    - Correlation ID propagation for end-to-end tracing
    """

    def __init__(
        self,
        workspace_id: str = "default",
        alpha: float = _DEFAULT_ALPHA,
        rrf_k: int = _RRF_K,
    ):
        self.workspace_id = workspace_id
        self.alpha = max(0.0, min(1.0, alpha))
        self.rrf_k = rrf_k
        self.dense_retriever = DenseRetriever(workspace_id)
        self.bm25_retriever = BM25Retriever(workspace_id)
        logger.info(f"HybridRetriever initialized: workspace={workspace_id}, " f"alpha={alpha}, rrf_k={rrf_k}")

    # ✅ NEW: Helper for Python 3.8 compatibility
    async def _run_in_thread(self, func, *args, **kwargs):
        """Run blocking function in thread pool — compatible with Python 3.8+."""
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(func, *args, **kwargs)
        else:
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in 3.10+
            return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def search_async(
        self,
        query: str,
        query_embedding: Optional[list[float]] = None,
        k: int = _DEFAULT_TOP_K,
        filter_dict: Optional[dict[str, any]] = None,
        correlation_id: Optional[str] = None,
        timeout_seconds: float = _RETRIEVER_TIMEOUT_SECONDS,  # ✅ NEW: Per-retriever timeout
    ) -> list[HybridRetrievalResult]:
        """
        Async: Hybrid search combining dense and sparse retrieval.
        ✅ FIXED: Added timeout protection on concurrent tasks.
        """
        corr_id = correlation_id or generate_retrieval_correlation_id("hybrid")
        k = validate_top_k(k, max_k=100)

        if not query and not query_embedding:
            logger.warning(f"[{corr_id}] Empty query for hybrid retrieval")
            return []

        try:
            dense_task = None
            sparse_task = None

            if query_embedding:
                dense_task = asyncio.create_task(
                    asyncio.wait_for(
                        self.dense_retriever.search_async(
                            query_embedding=query_embedding,
                            k=k * 2,
                            filter_dict=filter_dict,
                            correlation_id=corr_id,
                        ),
                        timeout=timeout_seconds,
                    )
                )

            if query:
                sparse_task = asyncio.create_task(
                    asyncio.wait_for(
                        self._run_in_thread(
                            self.bm25_retriever.search,
                            query=query,
                            k=k * 2,
                            filter_dict=filter_dict,
                            correlation_id=corr_id,
                        ),
                        timeout=timeout_seconds,
                    )
                )

            # Gather results with error handling
            dense_results: list[DenseRetrievalResult] = []
            sparse_results: list[BM25RetrievalResult] = []

            if dense_task:
                try:
                    dense_results = await dense_task
                except asyncio.TimeoutError:
                    logger.warning(f"[{corr_id}] Dense retrieval timed out after {timeout_seconds}s")
                except Exception as e:
                    logger.warning(f"[{corr_id}] Dense retrieval failed: {e}")

            if sparse_task:
                try:
                    sparse_results = await sparse_task
                except asyncio.TimeoutError:
                    logger.warning(f"[{corr_id}] Sparse retrieval timed out after {timeout_seconds}s")
                except Exception as e:
                    logger.warning(f"[{corr_id}] Sparse retrieval failed: {e}")

            # Apply RRF fusion
            fused_scores = reciprocal_rank_fusion(
                results=[
                    [r.to_dict() for r in dense_results],
                    [r.to_dict() for r in sparse_results],
                ],
                k=self.rrf_k,
                weights=[self.alpha, 1 - self.alpha],
            )

            # Build results map from both sources
            results_map: dict[str, dict[str, any]] = {}

            for r in dense_results:
                results_map[r.chunk_id] = {
                    "chunk_id": r.chunk_id,
                    "dense_score": r.score,
                    "sparse_score": 0.0,
                    "metadata": r.metadata,
                }

            for r in sparse_results:
                if r.chunk_id in results_map:
                    results_map[r.chunk_id]["sparse_score"] = r.score
                else:
                    results_map[r.chunk_id] = {
                        "chunk_id": r.chunk_id,
                        "dense_score": 0.0,
                        "sparse_score": r.score,
                        "metadata": r.metadata,
                    }

            # ✅ FIXED: Handle chunks in results_map but missing from fused_scores
            hybrid_results = []
            for chunk_id, data in results_map.items():
                if chunk_id in fused_scores:
                    score = fused_scores[chunk_id]
                else:
                    # Fallback: compute simple weighted score if RRF missed this chunk
                    score = self.alpha * data["dense_score"] + (1 - self.alpha) * data["sparse_score"]
                    logger.debug(f"[{corr_id}] Chunk {chunk_id} not in RRF output — using fallback score {score:.4f}")

                hybrid_results.append(
                    HybridRetrievalResult(
                        chunk_id=chunk_id,
                        score=score,
                        dense_score=data["dense_score"],
                        sparse_score=data["sparse_score"],
                        metadata=data["metadata"],
                        correlation_id=corr_id,
                    )
                )

            # Sort by fused score descending and return top-k
            hybrid_results.sort(key=lambda r: r.score, reverse=True)
            final_results = hybrid_results[:k]

            logger.debug(f"[{corr_id}] Hybrid retrieval: {len(final_results)} results")
            return final_results

        except Exception as e:
            logger.error(f"[{corr_id}] Hybrid retrieval failed: {type(e).__name__}: {e}")
            return []

    def search(
        self,
        query: str,
        query_embedding: Optional[list[float]] = None,
        k: int = _DEFAULT_TOP_K,
        filter_dict: Optional[dict[str, any]] = None,
        correlation_id: Optional[str] = None,
    ) -> list[HybridRetrievalResult]:
        """
        Sync wrapper — use search_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ HybridRetriever.search() called from async context — "
                "use search_async() instead. Returning empty results."
            )
            return []
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(self.search_async(query, query_embedding, k, filter_dict, correlation_id))


# DVMELTSS-M: Explicit module exports
__all__ = ["HybridRetriever", "HybridRetrievalResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
