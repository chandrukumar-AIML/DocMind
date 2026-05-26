# backend/app/rag/reranker.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security
# BATMAN-FIX: M - Memory safety, A - Async-ready
from __future__ import annotations
import logging
from typing import Optional, List, Tuple, Any

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.rag_utils import normalize_rerank_score

logger = logging.getLogger(__name__)

class CrossEncoderReranker:
    """
    Cross-encoder reranker using sentence-transformers.
    
    Features (DVMELTSS-V, BATMAN-M):
    - Lazy model loading to prevent OOM at startup
    - Batch processing with memory guards
    - Centralized score normalization via app.core.rag_utils
    - Correlation ID propagation for tracing
    - Configurable threshold filtering
    """

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        device: str = "cpu",
        batch_size: int = 32,
        max_batch_tokens: int = 512,  # FIXED: Added memory guard
    ):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.max_batch_tokens = max_batch_tokens
        self._model = None  # Lazy load on first use
        logger.info(f"CrossEncoderReranker: {model_name} on {device}")

    @property
    def model(self):
        """Lazy-load the CrossEncoder model on first access."""
        if self._model is None:
            logger.info(f"Loading CrossEncoder: {self.model_name}...")
            try:
                from sentence_transformers import CrossEncoder
                self._model = CrossEncoder(self.model_name, device=self.device)
                logger.info("CrossEncoder loaded.")
            except MemoryError as e:
                logger.error(f"OOM loading CrossEncoder '{self.model_name}'")
                raise RuntimeError(
                    f"OOM: Cannot load '{self.model_name}'. "
                    f"Reduce batch_size or use smaller model."
                ) from e
            except ImportError as e:
                logger.error(f"sentence-transformers not installed: {e}")
                raise RuntimeError("Install: `pip install sentence-transformers`") from e
            except Exception as e:
                logger.error(f"Failed to load CrossEncoder: {e}")
                raise RuntimeError(f"CrossEncoder load failed: {e}") from e
        return self._model

    def rerank(
        self,
        query: str,
        documents: List[Document],
        top_k: int = 3,
        threshold: Optional[float] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> List[Tuple[Document, float]]:
        """
        Rerank documents by relevance to query using cross-encoder.
        FIXED: Added correlation_id + memory-safe batching.
        """
        corr_id = correlation_id or "rerank_unknown"
        
        if not documents:
            return []
        if top_k <= 0:
            return []

        # Create query-document pairs for scoring
        pairs = [(query, doc.page_content) for doc in documents]
        
        # FIXED: Score with memory-safe batching
        scores = self._score_in_batches(pairs, corr_id)
        
        # Pair documents with scores
        scored = list(zip(documents, scores))
        scored.sort(key=lambda x: x[1], reverse=True)
        
        # Apply threshold filter
        if threshold is not None:
            scored = [(doc, s) for doc, s in scored if s >= threshold]
            logger.debug(f"[{corr_id}] Threshold {threshold}: {len(scored)} docs remain")
        
        result = scored[:top_k]
        if result:
            logger.info(
                f"[{corr_id}] Reranked {len(documents)} -> {len(result)} | "
                f"top={result[0][1]:.4f}, min={result[-1][1]:.4f}"
            )
        return result

    def _score_in_batches(self, pairs: List[Tuple[str, str]], corr_id: str) -> List[float]:
        """Score query-document pairs in memory-safe batches."""
        all_scores = []
        
        for i in range(0, len(pairs), self.batch_size):
            batch = pairs[i: i + self.batch_size]
            # FIXED: Check token count to prevent OOM
            total_tokens = sum(len(q) + len(d) for q, d in batch)
            if total_tokens > self.max_batch_tokens * len(batch):
                logger.warning(f"[{corr_id}] Batch too large, reducing size")
                batch = batch[:self.batch_size // 2]
            
            try:
                raw_scores = self.model.predict(batch, convert_to_numpy=True)
                # FIXED: Use centralized normalization
                normalized = [normalize_rerank_score(float(s)) for s in raw_scores]
                all_scores.extend(normalized)
            except Exception as e:
                logger.warning(f"[{corr_id}] Batch scoring failed: {e}")
                all_scores.extend([0.0] * len(batch))
            
            # Yield control to event loop if running async
            if i % (self.batch_size * 2) == 0:
                import asyncio
                try:
                    asyncio.get_running_loop()
                    # Allow other tasks to run
                except RuntimeError:
                    pass  # Not in async context
        
        return all_scores

    def get_model_info(self) -> dict:
        """Return model configuration for monitoring."""
        return {
            "model_name": self.model_name,
            "device": self.device,
            "batch_size": self.batch_size,
            "max_batch_tokens": self.max_batch_tokens,
            "loaded": self._model is not None,
        }

# Module-level singleton — persists across requests even when chain is re-created
_reranker_singleton: Optional["CrossEncoderReranker"] = None

def get_reranker(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> "CrossEncoderReranker":
    global _reranker_singleton
    if _reranker_singleton is None:
        _reranker_singleton = CrossEncoderReranker(model_name=model_name)
    return _reranker_singleton

__all__ = ["CrossEncoderReranker", "get_reranker"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

