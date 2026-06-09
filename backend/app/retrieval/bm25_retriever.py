# backend/app/retrieval/bm25_retriever.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular
# BATMAN-FIX: M - Memory safety, T - Batch processing
# ✅ FIXED: Numpy import + pure-Python argsort fallback
# ✅ FIXED: O(1) doc_id lookup via _id_map (not text comparison)
# ✅ FIXED: Index versioning + timestamp for stale detection
# ✅ FIXED: Robust _matches_filter for lists/nested/nulls
# ✅ FIXED: Memory-safe index rebuild with clear_index()
# ✅ FIXED: Unicode-aware tokenization option
# ✅ FIXED: Dataclass field name typo (meta -> metadata)

from __future__ import annotations
import logging
import re
import time
from dataclasses import dataclass
from typing import Final, Optional
from rank_bm25 import BM25Okapi

# DVMELTSS-M: Import centralized utilities
from app.core.retrieval_utils import validate_top_k, generate_retrieval_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: BM25 configuration
_DEFAULT_TOP_K: Final = 20
_MAX_TOP_K: Final = 100
_MIN_TOKEN_LENGTH: Final = 2

# ✅ Numpy with fallback
try:
    import numpy as np

    def _argsort_desc(arr: list[float], k: int) -> list[int]:
        """Get indices of top-k values in descending order."""
        return np.argsort(arr)[-k:][::-1].tolist()
except ImportError:
    # Pure-Python fallback (slower but no dependency)
    def _argsort_desc(arr: list[float], k: int) -> list[int]:
        indexed = list(enumerate(arr))
        indexed.sort(key=lambda x: x[1], reverse=True)
        return [idx for idx, _ in indexed[:k]]

    logger.warning("⚠️ numpy not available — using pure-Python argsort (slower)")


@dataclass(frozen=True)
class BM25RetrievalResult:
    """Immutable result from BM25 keyword retrieval."""

    chunk_id: str
    score: float
    metadata: dict[str, any]  # ✅ FIXED: Correct field name + syntax
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict[str, any]:
        return {
            "id": self.chunk_id,
            "score": round(self.score, 4),
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
        }


class BM25Retriever:
    """
    Sparse keyword retrieval using BM25 algorithm.

    Features (DVMELTSS-V, BATMAN-M):
    - Memory-efficient tokenization with Unicode support
    - Workspace-scoped document indexing with versioning
    - O(1) doc_id lookup via index map
    - Correlation ID tracing for audit trails
    """

    def __init__(self, workspace_id: str = "default", use_unicode: bool = True):
        self.workspace_id = workspace_id
        self.use_unicode = use_unicode
        self._bm25: Optional[BM25Okapi] = None
        self._corpus: list[str] = []
        self._doc_index: dict[str, dict[str, any]] = {}
        self._id_map: dict[int, str] = {}
        self._index_version: int = 0
        self._indexed_at: Optional[float] = None
        logger.info(f"BM25Retriever initialized: workspace={workspace_id}, unicode={use_unicode}")

    def _tokenize(self, text: str) -> list[str]:
        """Memory-safe tokenization with Unicode support."""
        if not text:
            return []

        if self.use_unicode:
            # FIXED: Removed redundant re.sub after re.findall(\w+) — \w already excludes
            # non-word chars, so the sub() call was a no-op that wasted CPU per token
            tokens = re.findall(
                r"\b[\w]{" + str(_MIN_TOKEN_LENGTH) + r",}\b",
                text.lower(),
                flags=re.UNICODE,
            )
            return tokens
        else:
            return re.findall(r"\b[a-z0-9]{%d,}\b" % _MIN_TOKEN_LENGTH, text.lower())

    def clear_index(self) -> None:
        """Free memory by clearing index structures."""
        self._corpus = []
        self._doc_index = {}
        self._id_map = {}
        self._bm25 = None
        logger.debug(f"BM25 index cleared for workspace={self.workspace_id}")

    def index_documents(self, documents: list[dict[str, any]]) -> None:
        """Build BM25 index from documents."""
        if not documents:
            logger.warning("No documents to index for BM25")
            return

        self.clear_index()

        self._corpus = []
        self._doc_index = {}
        self._id_map = {}

        for doc in documents:
            doc_id = doc.get("chunk_id") or doc.get("id")
            if not doc_id:
                continue

            text = doc.get("text") or doc.get("page_content", "")
            if not text:
                continue

            tokens = self._tokenize(text)
            if not tokens:
                continue

            self._doc_index[doc_id] = {
                "text": text,
                "metadata": doc.get("metadata", {}),
                "tokens": tokens,
            }

            idx = len(self._corpus)
            self._id_map[idx] = doc_id
            self._corpus.append(tokens)

        if self._corpus:
            self._bm25 = BM25Okapi(self._corpus)
            self._index_version += 1
            self._indexed_at = time.time()
            logger.info(
                f"BM25 index built: {len(self._corpus)} documents | "
                f"version={self._index_version} | workspace={self.workspace_id}"
            )
        else:
            logger.warning("BM25 index empty after processing documents")

    def get_index_info(self) -> dict[str, any]:
        """Return index metadata for monitoring/stale detection."""
        return {
            "document_count": len(self._doc_index),
            "version": self._index_version,
            "indexed_at": self._indexed_at,
            "workspace_id": self.workspace_id,
            "unicode_tokenization": self.use_unicode,
        }

    def search(
        self,
        query: str,
        k: int = _DEFAULT_TOP_K,
        filter_dict: Optional[dict[str, any]] = None,
        correlation_id: Optional[str] = None,
        max_age_seconds: Optional[float] = None,
    ) -> list[BM25RetrievalResult]:
        """Search using BM25 keyword matching."""
        corr_id = correlation_id or generate_retrieval_correlation_id("bm25")
        k = validate_top_k(k, max_k=_MAX_TOP_K)

        if max_age_seconds and self._indexed_at:
            age = time.time() - self._indexed_at
            if age > max_age_seconds:
                logger.warning(f"[{corr_id}] BM25 index stale: {age:.0f}s > {max_age_seconds}s — consider rebuilding")

        if not self._bm25 or not query:
            return []

        try:
            query_tokens = self._tokenize(query)
            if not query_tokens:
                return []

            scores = self._bm25.get_scores(query_tokens)
            top_indices = _argsort_desc(scores.tolist() if hasattr(scores, "tolist") else scores, k)

            results = []
            for idx in top_indices:
                if idx < 0 or idx >= len(self._corpus):
                    continue

                doc_id = self._id_map.get(idx)
                if not doc_id or doc_id not in self._doc_index:
                    continue

                doc_info = self._doc_index[doc_id]
                metadata = doc_info.get("metadata", {}) or {}

                if filter_dict and not self._matches_filter(metadata, filter_dict):
                    continue

                results.append(
                    BM25RetrievalResult(
                        chunk_id=doc_id,
                        score=float(scores[idx]),
                        metadata=metadata,
                        correlation_id=corr_id,
                    )
                )

            logger.debug(f"[{corr_id}] BM25 retrieval: {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"[{corr_id}] BM25 retrieval failed: {type(e).__name__}: {e}")
            return []

    def _matches_filter(self, metadata: dict, filter_dict: dict) -> bool:
        """Check if metadata matches all filter conditions."""
        for key, expected in filter_dict.items():
            actual = metadata.get(key)

            if expected is None:
                if actual is not None:
                    return False
                continue
            if actual is None:
                return False

            if isinstance(expected, dict):
                for op, op_val in expected.items():
                    if op == "$gte" and not (self._safe_compare(actual, op_val, ">=")):
                        return False
                    elif op == "$lte" and not (self._safe_compare(actual, op_val, "<=")):
                        return False
                    elif op == "$gt" and not (self._safe_compare(actual, op_val, ">")):
                        return False
                    elif op == "$lt" and not (self._safe_compare(actual, op_val, "<")):
                        return False
                    elif op == "$eq" and actual != op_val:
                        return False
                    elif op == "$ne" and actual == op_val:
                        return False
                    elif op == "$in" and actual not in (op_val if isinstance(op_val, list) else [op_val]):
                        return False
                    elif op == "$nin" and actual in (op_val if isinstance(op_val, list) else [op_val]):
                        return False
                continue

            if isinstance(actual, list):
                if expected in actual:
                    continue
                if isinstance(expected, dict):
                    if any(self._matches_filter({"item": item}, {"item": expected}) for item in actual):
                        continue
                return False

            if not self._safe_compare(actual, expected, "=="):
                return False

        return True

    def _safe_compare(self, a: any, b: any, op: str) -> bool:
        """Safe comparison with type coercion for numbers."""
        try:
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if op == "==":
                    return a == b
                if op == "!=":
                    return a != b
                if op == ">":
                    return a > b
                if op == ">=":
                    return a >= b
                if op == "<":
                    return a < b
                if op == "<=":
                    return a <= b
            elif isinstance(a, str) and isinstance(b, str):
                if op == "==":
                    return a == b
                if op == "!=":
                    return a != b
            else:
                if op == "==":
                    return a == b
                if op == "!=":
                    return a != b
        except Exception:
            pass
        return False


# DVMELTSS-M: Explicit module exports
__all__ = ["BM25Retriever", "BM25RetrievalResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
