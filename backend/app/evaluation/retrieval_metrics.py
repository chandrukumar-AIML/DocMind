
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Final, List, Optional, Any, Callable, Set, Union

import numpy as np

# DVMELTSS-M: Import centralized utilities
try:
    from app.core.eval_utils import aggregate_metrics, generate_eval_correlation_id

    _HAS_AGGREGATE_METRICS = True
except ImportError:
    _HAS_AGGREGATE_METRICS = False

    # ✅ Fallback: simple aggregation without CI
    def aggregate_metrics(values: List[float], metric_name: str, min_samples: int = 10) -> dict:
        """Simple fallback aggregation without confidence intervals."""
        if not values:
            return {"mean": 0.0, "ci_95_lower": 0.0, "ci_95_upper": 0.0}
        mean_val = float(np.mean(values))
        std_val = float(np.std(values)) if len(values) > 1 else 0.0
        # Simple 95% CI approximation: mean ± 2*std/sqrt(n)
        margin = 2 * std_val / np.sqrt(len(values)) if len(values) > 1 else 0.0
        return {
            "mean": round(mean_val, 4),
            "ci_95_lower": round(max(0.0, mean_val - margin), 4),
            "ci_95_upper": round(min(1.0, mean_val + margin), 4),
        }


logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Metrics for a single retrieval query."""

    query: str
    retrieved_ids: List[str]
    relevant_ids: Set[str]
    k: int
    correlation_id: str = ""

    @property
    def precision_at_k(self) -> float:
        """Precision@K: fraction of retrieved docs that are relevant."""
        if not self.retrieved_ids or self.k <= 0:
            return 0.0
        hits = sum(1 for cid in self.retrieved_ids[: self.k] if cid in self.relevant_ids)
        return hits / min(self.k, len(self.retrieved_ids))

    @property
    def recall_at_k(self) -> float:
        """Recall@K: fraction of relevant docs that were retrieved."""
        if not self.relevant_ids:
            return 1.0
        hits = sum(1 for cid in self.retrieved_ids[: self.k] if cid in self.relevant_ids)
        return hits / len(self.relevant_ids)

    @property
    def reciprocal_rank(self) -> float:
        """Reciprocal Rank: 1/rank of first relevant doc, or 0 if none."""
        for rank, cid in enumerate(self.retrieved_ids, start=1):
            if cid in self.relevant_ids:
                return 1.0 / rank
        return 0.0

    @property
    def hit_at_k(self) -> bool:
        """Hit@K: whether at least one relevant doc was retrieved in top-K."""
        return any(cid in self.relevant_ids for cid in self.retrieved_ids[: self.k])

    @property
    def mean_rank(self) -> Optional[float]:
        """Mean rank of relevant docs (for MRR alternative)."""
        ranks = [rank for rank, cid in enumerate(self.retrieved_ids, start=1) if cid in self.relevant_ids]
        return float(np.mean(ranks)) if ranks else None

    def to_dict(self) -> dict:
        """Convert to API-friendly dict."""
        return {
            "query": self.query[:80] + ("..." if len(self.query) > 80 else ""),
            "precision_at_k": round(self.precision_at_k, 4),
            "recall_at_k": round(self.recall_at_k, 4),
            "reciprocal_rank": round(self.reciprocal_rank, 4),
            "hit_at_k": self.hit_at_k,
            "k": self.k,
            "mean_rank": round(self.mean_rank, 4) if self.mean_rank else None,
            "correlation_id": self.correlation_id,
        }


@dataclass
class RetrievalEvalSuite:
    """Aggregated retrieval metrics for a dataset."""

    results: List[RetrievalResult] = field(default_factory=list)
    MIN_SAMPLES_FOR_VALID_EVAL: Final = 10
    correlation_id: str = ""

    def add(self, result: RetrievalResult):
        """Add a single retrieval result to the suite."""
        self.results.append(result)

    @property
    def mean_precision_at_k(self) -> float:
        """Mean Precision@K across all queries."""
        if not self.results:
            return 0.0
        return float(np.mean([r.precision_at_k for r in self.results]))

    @property
    def mean_recall_at_k(self) -> float:
        """Mean Recall@K across all queries."""
        if not self.results:
            return 0.0
        return float(np.mean([r.recall_at_k for r in self.results]))

    @property
    def mean_reciprocal_rank(self) -> float:
        """Mean Reciprocal Rank (MRR) across all queries."""
        if not self.results:
            return 0.0
        return float(np.mean([r.reciprocal_rank for r in self.results]))

    @property
    def hit_rate(self) -> float:
        """Hit@K rate: fraction of queries with at least one relevant hit."""
        if not self.results:
            return 0.0
        return float(np.mean([r.hit_at_k for r in self.results]))

    def summary(self) -> dict[str, Any]:
        """Return aggregated metrics summary with confidence intervals."""
        n = len(self.results)

        if n < self.MIN_SAMPLES_FOR_VALID_EVAL:
            logger.warning(
                f"[{self.correlation_id}] Evaluation has only {n} samples — "
                f"results not statistically reliable. Minimum recommended: {self.MIN_SAMPLES_FOR_VALID_EVAL}."
            )

        precision_values = [r.precision_at_k for r in self.results]
        recall_values = [r.recall_at_k for r in self.results]
        mrr_values = [r.reciprocal_rank for r in self.results]

        precision_ci = aggregate_metrics(
            precision_values,
            "precision_at_k",
            min_samples=self.MIN_SAMPLES_FOR_VALID_EVAL,
        )
        recall_ci = aggregate_metrics(recall_values, "recall_at_k", min_samples=self.MIN_SAMPLES_FOR_VALID_EVAL)
        mrr_ci = aggregate_metrics(mrr_values, "reciprocal_rank", min_samples=self.MIN_SAMPLES_FOR_VALID_EVAL)

        return {
            "n_queries": n,
            "statistically_valid": n >= self.MIN_SAMPLES_FOR_VALID_EVAL,
            "mean_precision_at_k": precision_ci.get("mean", 0.0),
            "precision_at_k_ci_95": (
                precision_ci.get("ci_95_lower", 0.0),
                precision_ci.get("ci_95_upper", 1.0),
            ),
            "mean_recall_at_k": recall_ci.get("mean", 0.0),
            "recall_at_k_ci_95": (
                recall_ci.get("ci_95_lower", 0.0),
                recall_ci.get("ci_95_upper", 1.0),
            ),
            "mean_reciprocal_rank": mrr_ci.get("mean", 0.0),
            "reciprocal_rank_ci_95": (
                mrr_ci.get("ci_95_lower", 0.0),
                mrr_ci.get("ci_95_upper", 1.0),
            ),
            "hit_rate": round(self.hit_rate, 4),
            "correlation_id": self.correlation_id,
        }

    def _validate_parent_refs(self, child_chunks, parent_chunks):
        """Cross-reference child parent_ids against provided parents."""
        parent_ids = {p.metadata.get("chunk_id") for p in parent_chunks}
        orphaned = []
        for child in child_chunks:
            pid = child.metadata.get("parent_id", "")
            if pid and pid not in parent_ids:
                orphaned.append((child.metadata.get("chunk_id"), pid))
                logger.warning(
                    f"[{self.correlation_id}] Child chunk '{child.metadata.get('chunk_id')}' "
                    f"references orphaned parent_id='{pid}'"
                )
        return orphaned


def _validate_eval_inputs(
    ground_truth: List[dict],
    retrieve_fn: Callable,
    k: int,
    corr_id: str,
) -> tuple[bool, str]:
    """Validate evaluation inputs before processing."""
    if not isinstance(ground_truth, list):
        return False, "ground_truth must be a list"
    if not callable(retrieve_fn):
        return False, "retrieve_fn must be a callable"
    if not isinstance(k, int) or k <= 0:
        return False, "k must be a positive integer"
    return True, ""


class RetrievalEvaluator:
    """
    Evaluates retrieval quality against a ground truth dataset.

    Features:
    - Precision@K, Recall@K, MRR, Hit@K metrics
    - Bootstrap confidence intervals for statistical reliability
    - Async-safe timeout protection for slow retrieval functions
    - Parent-child reference validation for debugging
    - Correlation ID propagation for tracing
    """

    async def evaluate(
        self,
        ground_truth: List[dict],
        retrieve_fn: Callable[[str, int], Union[List[Any], Any]],
        k: int = 3,
        timeout_seconds: int = 30,
        concurrency: int = 3,
        correlation_id: Optional[str] = None,
    ) -> RetrievalEvalSuite:
        """Evaluate retrieval function against ground truth dataset."""
        corr_id = correlation_id or generate_eval_correlation_id("retrieval_eval")

        # ✅ Validate inputs
        is_valid, error = _validate_eval_inputs(ground_truth, retrieve_fn, k, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid eval inputs: {error}")
            return RetrievalEvalSuite(correlation_id=corr_id)

        suite = RetrievalEvalSuite(correlation_id=corr_id)
        semaphore = asyncio.Semaphore(concurrency)

        async def evaluate_query(item: dict) -> RetrievalResult:
            async with semaphore:
                query = item.get("query", "")
                relevant_ids = set(item.get("relevant_chunk_ids", []))

                try:
                    if inspect.iscoroutinefunction(retrieve_fn):
                        retrieved_docs = await asyncio.wait_for(
                            retrieve_fn(query=query, k=k),
                            timeout=timeout_seconds,
                        )
                    else:
                        # Run sync retrieve_fn in thread with timeout
                        retrieved_docs = await asyncio.wait_for(
                            asyncio.to_thread(lambda: retrieve_fn(query=query, k=k)),
                            timeout=timeout_seconds,
                        )

                    # ✅ Safe extraction of chunk IDs
                    retrieved_ids = []
                    for j, doc in enumerate(retrieved_docs or []):
                        if hasattr(doc, "metadata") and isinstance(doc.metadata, dict):
                            cid = doc.metadata.get("chunk_id")
                        elif isinstance(doc, dict):
                            cid = doc.get("chunk_id")
                        else:
                            cid = f"doc_{j}"
                        if cid:
                            retrieved_ids.append(str(cid))

                    return RetrievalResult(
                        query=query,
                        retrieved_ids=retrieved_ids,
                        relevant_ids=relevant_ids,
                        k=k,
                        correlation_id=corr_id,
                    )

                except asyncio.TimeoutError:
                    logger.error(f"[{corr_id}] Retrieval timed out after {timeout_seconds}s for: {query[:60]}")
                    return RetrievalResult(
                        query=query,
                        retrieved_ids=[],
                        relevant_ids=relevant_ids,
                        k=k,
                        correlation_id=corr_id,
                    )
                except Exception as e:
                    logger.error(f"[{corr_id}] Retrieval failed for: {query[:60]}: {e}")
                    return RetrievalResult(
                        query=query,
                        retrieved_ids=[],
                        relevant_ids=relevant_ids,
                        k=k,
                        correlation_id=corr_id,
                    )

        tasks = [evaluate_query(item) for item in ground_truth]

        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                suite.add(result)
                logger.debug(
                    f"[{corr_id}] P@{k}={result.precision_at_k:.3f} "
                    f"R@{k}={result.recall_at_k:.3f} "
                    f"RR={result.reciprocal_rank:.3f}"
                )
            except Exception as e:
                logger.error(f"[{corr_id}] Task completion failed: {e}")

        logger.info(f"[{corr_id}] Retrieval eval complete: {suite.summary()}")
        return suite


def get_retrieval_metrics_metadata() -> dict[str, Any]:
    """✅ NEW: Return retrieval metrics metadata for monitoring."""
    return {
        "metrics": [
            "precision_at_k",
            "recall_at_k",
            "reciprocal_rank",
            "hit_at_k",
            "mean_rank",
        ],
        "min_samples_for_valid_eval": RetrievalEvalSuite.MIN_SAMPLES_FOR_VALID_EVAL,
        "default_k": 3,
        "default_timeout_seconds": 30,
        "default_concurrency": 3,
        "uses_bootstrap_ci": _HAS_AGGREGATE_METRICS,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "RetrievalEvaluator",
    "RetrievalResult",
    "RetrievalEvalSuite",
    "get_retrieval_metrics_metadata",
]
# Local smoke test entry point. Run: python -m

