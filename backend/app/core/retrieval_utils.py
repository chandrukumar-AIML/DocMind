# backend/app/core/retrieval_utils.py
# DVMELTSS-FIX: M - Modular, V - Validate, A - Async
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - True async, M - Memory safety
"""
Shared utilities for retrieval modules.

Centralizes:
- Async-safe vector search with timeout guards
- RRF (Reciprocal Rank Fusion) scoring
- Hybrid retrieval weighting
- Correlation ID propagation

Usage:
    from app.core.retrieval_utils import reciprocal_rank_fusion, hybrid_score
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final, Optional, List, Dict, Any


from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: RRF configuration
_RRF_K: Final = 60  # Standard RRF constant
_DEFAULT_HYBRID_WEIGHT: Final = 0.5  # Balance between dense and sparse

# BATMAN-A: Search timeout defaults
_MAX_SEARCH_TIMEOUT: Final = 30.0
_MAX_CANDIDATES: Final = 200


def reciprocal_rank_fusion(
    results: List[List[Dict[str, Any]]],
    k: int = _RRF_K,
    weights: Optional[List[float]] = None,
) -> Dict[str, float]:
    """
    Apply Reciprocal Rank Fusion to merge multiple ranked result lists.

    Args:
        results: List of result lists, each containing dicts with 'id' and 'score'
        k: RRF constant (higher = less rank sensitivity)
        weights: Optional weights for each result list

    Returns:
        Dict mapping doc_id to fused score
    """
    fused_scores: Dict[str, float] = {}

    for i, result_list in enumerate(results):
        weight = weights[i] if weights and i < len(weights) else 1.0
        for rank, item in enumerate(result_list, start=1):
            doc_id = item.get("id") or item.get("chunk_id")
            if not doc_id:
                continue
            # RRF formula: score = weight / (k + rank)
            fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + weight / (k + rank)

    return fused_scores


def hybrid_score(
    dense_score: float,
    sparse_score: float,
    alpha: float = _DEFAULT_HYBRID_WEIGHT,
) -> float:
    """
    Compute weighted hybrid score combining dense and sparse retrieval.

    Args:
        dense_score: Score from dense/vector retrieval (0.0-1.0)
        sparse_score: Score from sparse/BM25 retrieval (0.0-1.0)
        alpha: Weight for dense score (1-alpha for sparse)

    Returns:
        Combined hybrid score (0.0-1.0)
    """
    # Normalize inputs to [0, 1] range
    dense_norm = max(0.0, min(1.0, dense_score))
    sparse_norm = max(0.0, min(1.0, sparse_score))

    # Weighted combination
    combined = alpha * dense_norm + (1 - alpha) * sparse_norm
    return max(0.0, min(1.0, combined))


async def safe_vector_search(
    search_fn,
    query_embedding: List[float],
    k: int,
    filter_dict: Optional[Dict] = None,
    timeout: float = _MAX_SEARCH_TIMEOUT,
    correlation_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Execute vector search with timeout guard and error handling.

    Args:
        search_fn: Async function that performs the actual search
        query_embedding: Query vector for similarity search
        k: Number of results to retrieve
        filter_dict: Optional metadata filters
        timeout: Maximum seconds to wait for search
        correlation_id: Request ID for tracing

    Returns:
        List of search results or empty list on timeout/error
    """
    corr_id = correlation_id or "retrieval_unknown"

    try:
        # Wrap search in timeout to prevent hanging
        results = await asyncio.wait_for(
            search_fn(query_embedding, k=k, filter_dict=filter_dict),
            timeout=timeout,
        )
        # Safety cap on result count
        return results[:_MAX_CANDIDATES] if results else []

    except asyncio.TimeoutError:
        logger.warning(f"[{corr_id}] Vector search timed out after {timeout}s")
        return []
    except Exception as e:
        logger.error(f"[{corr_id}] Vector search failed: {type(e).__name__}: {e}")
        return []


def generate_retrieval_correlation_id(prefix: str = "retrieval") -> str:
    """Generate correlation ID for retrieval operations."""
    return f"{prefix}_{generate_correlation_id()}"


def validate_top_k(k: int, min_k: int = 1, max_k: int = 100) -> int:
    """Validate and clamp top_k parameter."""
    if k < min_k:
        logger.warning(f"top_k={k} below minimum {min_k}, clamping to {min_k}")
        return min_k
    if k > max_k:
        logger.warning(f"top_k={k} above maximum {max_k}, clamping to {max_k}")
        return max_k
    return k


# DVMELTSS-M: Explicit module exports
__all__ = [
    "reciprocal_rank_fusion",
    "hybrid_score",
    "safe_vector_search",
    "generate_retrieval_correlation_id",
    "validate_top_k",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
