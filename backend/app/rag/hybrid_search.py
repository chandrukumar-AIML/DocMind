from __future__ import annotations
import logging
import pickle
from pathlib import Path
from typing import Any, Optional, List, Tuple

from langchain_core.documents import Document
from rank_bm25 import BM25Okapi

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.rag_utils import (
    tokenize_for_bm25,
    reciprocal_rank_fusion,
    validate_rag_weights,
)
from app.vectorstore.store_manager import VectorStoreManager

logger = logging.getLogger(__name__)


def _get_bm25_cache_path() -> Path:
    """ADDED: Resolve the configured BM25 cache path for tests and tooling."""
    settings = get_settings()
    return Path(settings.bm25_cache_path or ".cache/bm25_index.pkl")


class HybridSearcher:
    """
    Hybrid BM25 + semantic vector search with Reciprocal Rank Fusion.

    Features (DVMELTSS-V, BATMAN-M):
    - Centralized tokenization via app.core.rag_utils
    - Configurable RRF weights with validation
    - Persistent cache with atomic writes + settings-based path
    - Correlation ID propagation for tracing
    """

    def __init__(
        self,
        store_manager: VectorStoreManager,
        bm25_corpus: Optional[List[str]] = None,
        semantic_weight: float = 0.6,
        keyword_weight: float = 0.4,
        bm25_cache_path: Optional[Path] = None,
    ):
        self.store = store_manager
        # Per-workspace isolation: without an explicit override, this falls back to
        # the legacy global cache path (unchanged default behavior). Callers scoped to
        # a specific workspace should pass get_bm25_index_path(workspace_id) — otherwise
        # BM25 keyword hits would return other workspaces' actual document content.
        self._bm25_cache_path = bm25_cache_path
        self._bm25: Optional[BM25Okapi] = None
        self._bm25_docs: List[Document] = []

        self.semantic_weight, self.keyword_weight = validate_rag_weights(semantic_weight, keyword_weight)

        if bm25_corpus:
            self._build_bm25_index(bm25_corpus)
        elif not self._load_bm25_from_cache():
            logger.info("BM25 cache not found — will build on first call")

    def search(
        self,
        query: str,
        k: int = 20,
        filter_dict: Optional[dict[str, Any]] = None,
        hyde_query: Optional[str] = None,
        semantic_weight: Optional[float] = None,  # FIXED: Allow override
        keyword_weight: Optional[float] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> List[Tuple[Document, float]]:
        """
        Hybrid search combining semantic + keyword results via RRF.
        FIXED: Added correlation_id + configurable weights.
        """
        corr_id = correlation_id or "search_unknown"
        semantic_q = hyde_query or query

        # Use override weights or defaults
        s_weight, k_weight = validate_rag_weights(
            semantic_weight or self.semantic_weight,
            keyword_weight or self.keyword_weight,
        )

        semantic_results = self._semantic_search(semantic_q, k=k * 2, filter_dict=filter_dict)
        keyword_results = self._bm25_search(query, k=k * 2)

        if not semantic_results and not keyword_results:
            logger.debug(f"[{corr_id}] Hybrid search: no results")
            return []

        # RRF expects List[Tuple[item, score]] — wrap plain Document lists with rank-based scores
        def _to_scored(docs):
            return [(doc, 1.0 / (i + 1)) for i, doc in enumerate(docs)]

        fused = reciprocal_rank_fusion(
            ranked_lists=[_to_scored(semantic_results), _to_scored(keyword_results)],
            weights=[s_weight, k_weight],
            k=k,
        )

        logger.info(
            f"[{corr_id}] Hybrid: semantic={len(semantic_results)}, "
            f"keyword={len(keyword_results)}, fused={len(fused)}"
        )
        return fused

    def build_bm25_from_store(self, source_file: Optional[str] = None):
        """Build BM25 index from ChromaDB using public API."""
        if source_file:
            docs = self.store.chroma.get_document_chunks(source_file)
        else:
            docs = []
            for batch_docs, _ in self.store.chroma.get_all_chunks_with_embeddings(batch_size=500):
                docs.extend(batch_docs)

        if not docs:
            logger.info("BM25 index skipped — no documents")
            self._bm25 = None
            self._bm25_docs = []
            return

        self._bm25_docs = docs
        self._build_bm25_index([doc.page_content for doc in docs])
        logger.info(f"BM25 index built: {len(docs)} documents")

    def _build_bm25_index(self, corpus: List[str]):
        """Build BM25Okapi index from tokenized corpus."""
        if not corpus:
            logger.info("BM25 index skipped — empty corpus")
            self._bm25 = None
            return

        tokenized = [tokenize_for_bm25(text) for text in corpus]
        non_empty = [(tokens, doc) for tokens, doc in zip(tokenized, self._bm25_docs) if tokens]

        if not non_empty:
            logger.warning("BM25 index skipped — all documents empty after tokenization")
            self._bm25 = None
            self._bm25_docs = []
            return

        tokens_only = [t for t, _ in non_empty]
        self._bm25_docs = [doc for _, doc in non_empty]
        self._bm25 = BM25Okapi(tokens_only)
        self._persist_bm25_cache()

    def _persist_bm25_cache(self):
        """Save BM25 index to disk with atomic write."""
        if self._bm25 is None:
            return
        try:
            cache_path = self._bm25_cache_path or _get_bm25_cache_path()
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            with open(temp_path, "wb") as f:
                pickle.dump(self._bm25, f)
            temp_path.replace(cache_path)
            logger.debug(f"BM25 index cached to {cache_path}")
        except OSError as e:
            logger.warning(f"BM25 persist failed: {e}")
        except Exception as e:
            logger.warning(f"BM25 cache unexpected error: {e}")

    def _load_bm25_from_cache(self) -> bool:
        """Load BM25 index from disk cache."""
        cache_path = self._bm25_cache_path or _get_bm25_cache_path()
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    self._bm25 = pickle.load(f)
                logger.info(f"BM25 index loaded from cache: {cache_path}")
                return True
            except Exception as e:
                logger.warning(f"BM25 cache load failed: {e}")
                try:
                    cache_path.unlink()
                except OSError:
                    pass
        return False

    def clear_cache(self):
        """Clear BM25 cache file."""
        cache_path = self._bm25_cache_path or _get_bm25_cache_path()
        if cache_path.exists():
            try:
                cache_path.unlink()
                logger.info(f"BM25 cache cleared: {cache_path}")
            except OSError as e:
                logger.warning(f"Failed to clear BM25 cache: {e}")

    def _semantic_search(self, query: str, k: int, filter_dict=None) -> List[Document]:
        """Delegate semantic search to vector store manager."""
        results = self.store.search(query=query, k=k, filter_dict=filter_dict)
        return [doc for doc, _ in results]

    def _bm25_search(self, query: str, k: int) -> List[Document]:
        """Search using BM25 keyword matching."""
        if self._bm25 is None or not self._bm25_docs:
            return []

        tokenized_query = tokenize_for_bm25(query)
        if not tokenized_query:
            return []

        scores = self._bm25.get_scores(tokenized_query)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]

        return [self._bm25_docs[i] for i in top_indices if scores[i] > 0 and i < len(self._bm25_docs)]

    def get_model_info(self) -> dict:
        """Return model configuration for monitoring."""
        return {
            "bm25_ready": self._bm25 is not None,
            "semantic_weight": self.semantic_weight,
            "keyword_weight": self.keyword_weight,
        }


# DVMELTSS-M: Explicit module exports
__all__ = ["HybridSearcher", "_get_bm25_cache_path"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.rag.hybrid_search) ---
# ========================================================================

