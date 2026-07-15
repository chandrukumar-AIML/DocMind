from __future__ import annotations
import json
import logging
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
        """Save BM25 index to disk with atomic write using safe JSON serialization.

        SECURITY: Never use pickle — BM25 state is pure numeric data (ints, floats,
        dicts, lists) that serialises cleanly to JSON without code execution risk.
        """
        if self._bm25 is None:
            return
        try:
            cache_path = self._bm25_cache_path or _get_bm25_cache_path()
            # Use .json extension — old .pkl files are left in place until explicitly cleared
            cache_path = cache_path.with_suffix(".json")
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp.json")

            state = {
                "version": 1,
                "corpus_size": self._bm25.corpus_size,
                "avgdl": self._bm25.avgdl,
                "doc_len": list(self._bm25.doc_len),
                # doc_freqs: list of Counter → list of plain dict
                "doc_freqs": [dict(df) for df in self._bm25.doc_freqs],
                "idf": dict(self._bm25.idf),
                "k1": float(self._bm25.k1),
                "b": float(self._bm25.b),
                "epsilon": float(self._bm25.epsilon),
            }
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            temp_path.replace(cache_path)
            logger.debug(f"BM25 index cached to {cache_path}")
        except OSError as e:
            logger.warning(f"BM25 persist failed: {e}")
        except Exception as e:
            logger.warning(f"BM25 cache unexpected error: {e}")

    def _load_bm25_from_cache(self) -> bool:
        """Load BM25 index from safe JSON disk cache.

        SECURITY: JSON deserialization cannot execute arbitrary code, unlike pickle.
        Legacy .pkl files are intentionally ignored — they must be rebuilt from ChromaDB.
        """
        cache_path = self._bm25_cache_path or _get_bm25_cache_path()
        json_path = cache_path.with_suffix(".json")

        if not json_path.exists():
            # Warn if an old unsafe pickle file is present so operators can clean it up
            pkl_path = cache_path.with_suffix(".pkl") if cache_path.suffix != ".pkl" else cache_path
            if cache_path.exists() or pkl_path.exists():
                logger.warning(
                    "Legacy BM25 .pkl cache found but ignored for security (RCE risk). "
                    "The index will be rebuilt from ChromaDB automatically. "
                    f"Remove the old file manually: {cache_path}"
                )
            return False

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                state = json.load(f)

            if state.get("version") != 1:
                logger.warning(f"BM25 cache version mismatch: {state.get('version')} — rebuilding")
                json_path.unlink(missing_ok=True)
                return False

            # Reconstruct BM25Okapi from serialized state without calling fit()
            bm25 = BM25Okapi.__new__(BM25Okapi)
            bm25.corpus_size = int(state["corpus_size"])
            bm25.avgdl = float(state["avgdl"])
            bm25.doc_len = list(state["doc_len"])
            bm25.doc_freqs = [dict(df) for df in state["doc_freqs"]]
            bm25.idf = {k: float(v) for k, v in state["idf"].items()}
            bm25.k1 = float(state.get("k1", 1.5))
            bm25.b = float(state.get("b", 0.75))
            bm25.epsilon = float(state.get("epsilon", 0.25))
            # nd: number of documents each term appears in (derived from doc_freqs)
            bm25.nd = {term: sum(1 for df in bm25.doc_freqs if term in df) for term in bm25.idf}

            self._bm25 = bm25
            logger.info(f"BM25 index loaded from JSON cache: {json_path} ({bm25.corpus_size} docs)")
            return True
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"BM25 JSON cache corrupt: {e} — rebuilding")
            json_path.unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"BM25 cache load failed: {e}")
        return False

    def clear_cache(self):
        """Clear BM25 cache files (JSON and any legacy pkl)."""
        cache_path = self._bm25_cache_path or _get_bm25_cache_path()
        for path in (cache_path.with_suffix(".json"), cache_path, cache_path.with_suffix(".pkl")):
            if path.exists():
                try:
                    path.unlink()
                    logger.info(f"BM25 cache cleared: {path}")
                except OSError as e:
                    logger.warning(f"Failed to clear BM25 cache {path}: {e}")

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

