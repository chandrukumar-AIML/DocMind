# backend/app/retrieval/rrf_fusion.py
# DVMELTSS-FIX: M - Modular, V - Validate
# ASCALE-FIX: S - Separation
# ✅ FIXED: Weights validation + auto-normalize
# ✅ FIXED: Safe import fallback for reciprocal_rank_fusion
# ✅ FIXED: Warning log for items missing id/chunk_id
# ✅ FIXED: Optional score normalization + deterministic tie-breaking

from __future__ import annotations
import logging
from typing import Final, Optional
from dataclasses import dataclass

# DVMELTSS-M: Import centralized utilities with safe fallback
try:
    from app.core.retrieval_utils import (
        reciprocal_rank_fusion,
        generate_retrieval_correlation_id,
    )
except ImportError:
    # ✅ Fallback stub for graceful degradation
    def reciprocal_rank_fusion(results: list, k: int = 60, weights: list = None):
        """Simple RRF fallback: average reciprocal ranks."""
        fused = {}
        for i, result_list in enumerate(results):
            weight = weights[i] if weights and i < len(weights) else 1.0
            for rank, item in enumerate(result_list, start=1):
                doc_id = item.get("id") or item.get("chunk_id")
                if doc_id:
                    fused[doc_id] = fused.get(doc_id, 0) + weight / (rank + k)
        return fused

    def generate_retrieval_correlation_id(prefix: str = "retrieval") -> str:
        import time
        import secrets

        return f"{prefix}_{int(time.time())}_{secrets.token_hex(4)}"

    logging.warning("⚠️ retrieval_utils imports failed — using fallback RRF implementation")

logger = logging.getLogger(__name__)

_RRF_K: Final = 60


@dataclass(frozen=True)
class RRFFusionResult:
    """Result of RRF fusion operation."""

    doc_id: str
    fused_score: float
    source_rankings: dict[str, int]
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, any]:
        return {
            "id": self.doc_id,
            "fused_score": round(self.fused_score, 4),
            "source_rankings": self.source_rankings,
            "correlation_id": self.correlation_id,
        }


class RRFFusion:
    """
    Standalone Reciprocal Rank Fusion utility.

    Usage:
    fusion = RRFFusion(k=60)
    results = fusion.fuse([results_a, results_b], weights=[0.7, 0.3])
    """

    def __init__(self, k: int = _RRF_K, normalize_scores: bool = False):
        self.k = k
        self.normalize_scores = normalize_scores  # ✅ NEW: Optional score normalization
        logger.info(f"RRFFusion initialized: k={k}, normalize={normalize_scores}")

    def fuse(
        self,
        ranked_lists: list[list[dict[str, any]]],
        weights: Optional[list[float]] = None,
        correlation_id: Optional[str] = None,
        normalize_scores: Optional[bool] = None,  # Override instance default
    ) -> list[RRFFusionResult]:
        """
        Apply RRF to merge multiple ranked result lists.
        ✅ FIXED: Input validation + safe fallbacks + deterministic ordering.
        """
        corr_id = correlation_id or generate_retrieval_correlation_id("rrf")
        use_normalize = normalize_scores if normalize_scores is not None else self.normalize_scores

        if not ranked_lists:
            return []

        # ✅ Validate weights length matches ranked_lists
        if weights:
            if len(weights) != len(ranked_lists):
                logger.warning(
                    f"[{corr_id}] Weights length ({len(weights)}) != ranked_lists length ({len(ranked_lists)}) — auto-normalizing"
                )
                # Auto-normalize: equal weights
                weights = [1.0 / len(ranked_lists)] * len(ranked_lists)
            else:
                # Normalize weights to sum to 1.0 for consistent scoring
                total = sum(weights)
                if total > 0:
                    weights = [w / total for w in weights]

        # ✅ Validate each result item has required fields
        validated_lists = []
        for list_idx, result_list in enumerate(ranked_lists):
            validated = []
            for item in result_list:
                doc_id = item.get("id") or item.get("chunk_id")
                if not doc_id:
                    # ✅ Log warning for items missing identifier
                    logger.debug(f"[{corr_id}] Item in list_{list_idx} missing id/chunk_id — skipping")
                    continue
                validated.append({**item, "_doc_id": doc_id})  # Cache doc_id for speed
            validated_lists.append(validated)

        if not any(validated_lists):
            logger.warning(f"[{corr_id}] No valid items to fuse after validation")
            return []

        try:
            # Apply RRF to get fused scores
            fused_scores = reciprocal_rank_fusion(
                results=validated_lists,
                k=self.k,
                weights=weights,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] RRF fusion failed: {e}", exc_info=True)
            return []

        # ✅ Normalize scores to [0, 1] if requested
        if use_normalize and fused_scores:
            max_score = max(fused_scores.values())
            min_score = min(fused_scores.values())
            score_range = max_score - min_score if max_score != min_score else 1.0
            fused_scores = {
                doc_id: (score - min_score) / score_range if score_range > 0 else 1.0
                for doc_id, score in fused_scores.items()
            }

        # Track source rankings for each doc
        source_rankings: dict[str, dict[str, int]] = {}

        for list_idx, result_list in enumerate(validated_lists):
            source_name = f"list_{list_idx}"
            for rank, item in enumerate(result_list, start=1):
                doc_id = item["_doc_id"]  # Use cached id
                if doc_id not in source_rankings:
                    source_rankings[doc_id] = {}
                source_rankings[doc_id][source_name] = rank

        # Build fusion results with deterministic tie-breaking
        results = [
            RRFFusionResult(
                doc_id=doc_id,
                fused_score=score,
                source_rankings=source_rankings.get(doc_id, {}),
                correlation_id=corr_id,
            )
            for doc_id, score in fused_scores.items()
        ]

        # ✅ FIXED: Deterministic sort — by score desc, then doc_id asc for ties
        results.sort(key=lambda r: (-r.fused_score, r.doc_id))

        logger.debug(f"[{corr_id}] RRF fusion: {len(results)} results")
        return results

    def get_fusion_stats(self, results: list[RRFFusionResult]) -> dict[str, any]:
        """✅ NEW: Return fusion metadata for monitoring/debugging."""
        if not results:
            return {"count": 0, "score_range": (0, 0), "sources": {}}

        scores = [r.fused_score for r in results]
        source_counts: dict[str, int] = {}
        for r in results:
            for source in r.source_rankings:
                source_counts[source] = source_counts.get(source, 0) + 1

        return {
            "count": len(results),
            "score_range": (min(scores), max(scores)),
            "avg_score": sum(scores) / len(scores),
            "sources": source_counts,
            "k": self.k,
            "normalize": self.normalize_scores,
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["RRFFusion", "RRFFusionResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
