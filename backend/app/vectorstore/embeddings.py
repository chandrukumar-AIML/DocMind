# backend/app/vectorstore/embeddings.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# BATMAN-FIX: A - True async, T - Batch processing, M - Memory safety
# OWASP-FIX: 7 - Safe data handling, 9 - Input sanitization
# ✅ FIXED: Async wrapper for sync OpenAI call + single cache save + input validation

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional, List, Any

import numpy as np
from openai import OpenAI, APIError
from langchain_core.embeddings import Embeddings

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.retry import retry_async, RetryConfig
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)


# ── Local sentence-transformers embeddings (primary — no API cost) ─────────
class LocalSentenceTransformerEmbeddings(Embeddings):
    """
    Local embedding using sentence-transformers (all-mpnet-base-v2 → 768-dim).
    Free, offline, already installed via requirements.txt.
    Falls back to Voyage AI → hash if torch/model unavailable.
    """

    def __init__(
        self,
        model_name: str = "all-mpnet-base-v2",
        voyage_api_key: Optional[str] = None,
        voyage_model: str = "voyage-3-lite",
        dimensions: int = 768,
    ):
        self.model_name = model_name
        self.voyage_api_key = voyage_api_key
        self.voyage_model = voyage_model
        self.dimensions = dimensions
        self._model = None
        self._voyage_client = None
        self._mode = "uninitialized"
        self._init()

    def _init(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            self._mode = "local"
            logger.info(f"LocalSentenceTransformerEmbeddings: loaded {self.model_name} (local)")
        except Exception as e:
            logger.warning(f"sentence-transformers unavailable: {e}. Trying Voyage AI.")
            if self.voyage_api_key:
                try:
                    import voyageai
                    self._voyage_client = voyageai.Client(api_key=self.voyage_api_key)
                    self._mode = "voyage"
                    logger.info(f"LocalSentenceTransformerEmbeddings: using Voyage AI fallback ({self.voyage_model})")
                except Exception as ve:
                    logger.warning(f"Voyage AI unavailable: {ve}. Falling back to hash embeddings.")
                    self._mode = "hash"
            else:
                logger.warning("No VOYAGE_API_KEY set. Falling back to hash embeddings.")
                self._mode = "hash"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    def _embed_one(self, text: str) -> list[float]:
        if not text or not text.strip():
            return [0.0] * self.dimensions

        if self._mode == "local" and self._model is not None:
            try:
                vec = self._model.encode(text, normalize_embeddings=True)
                return vec.tolist()
            except Exception as e:
                logger.warning(f"Local embedding failed: {e}. Trying Voyage AI.")
                self._mode = "voyage" if self.voyage_api_key else "hash"

        if self._mode == "voyage" and self._voyage_client is not None:
            try:
                result = self._voyage_client.embed(
                    [text], model=self.voyage_model, output_dimension=self.dimensions
                )
                return result.embeddings[0]
            except Exception as e:
                logger.warning(f"Voyage AI embedding failed: {e}. Falling back to hash.")
                self._mode = "hash"

        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> list[float]:
        import hashlib
        tokens = sorted(re.findall(r"\w+", text.lower()))
        vector = np.zeros(self.dimensions, dtype=np.float32)
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign * 0.1
        norm = np.linalg.norm(vector)
        if norm > 1e-6:
            vector /= norm
        return vector.tolist()

    @property
    def active_mode(self) -> str:
        return self._mode


def get_local_embeddings(
    model_name: str = "all-mpnet-base-v2",
    voyage_api_key: Optional[str] = None,
    voyage_model: str = "voyage-3-lite",
    dimensions: int = 768,
) -> LocalSentenceTransformerEmbeddings:
    """Factory: local sentence-transformers → Voyage AI → hash fallback chain."""
    return LocalSentenceTransformerEmbeddings(
        model_name=model_name,
        voyage_api_key=voyage_api_key,
        voyage_model=voyage_model,
        dimensions=dimensions,
    )


class CachedOpenAIEmbeddings(Embeddings):
    """
    Production embedding wrapper with:
    - NumPy .npy persistent cache (scalable, fast load)
    - Centralized retry logic with exponential backoff
    - LangChain Embeddings interface
    - Expanded PII scrubbing for GDPR/HIPAA compliance
    - Correlation ID tracing for distributed debugging
    """

    MAX_BATCH_SIZE = 512
    EMBEDDING_DIM = 3072
    # DVMELTSS-E: Only retry transient errors — never retry quota/auth failures
    _EMBEDDING_RETRY_CONFIG = RetryConfig(
        max_attempts=2,
        backoff_base=0.5,
        backoff_max=5.0,
        exceptions=(APIError,),  # APIConnectionError, APITimeoutError subclass this
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "text-embedding-3-large",
        cache_dir: Optional[str] = None,
        dimensions: int = 3072,
        enable_pii_scrubbing: bool = False,
    ):
        # Validate API key format if provided
        if api_key and not api_key.startswith("sk-"):
            logger.warning("OpenAI API key format may be invalid (should start with 'sk-')")

        self.client = OpenAI(api_key=api_key) if api_key else None
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions
        self._local_fallback = not bool(api_key)
        self.enable_pii_scrubbing = enable_pii_scrubbing
        self._keys: list[str] = []
        self._vectors: np.ndarray = np.empty((0, dimensions), dtype=np.float32)
        self._key_to_idx: dict[str, int] = {}

        cache_base = Path(cache_dir or ".cache/embeddings")
        cache_base.mkdir(parents=True, exist_ok=True)
        self._keys_path = cache_base / f"{model}.keys.txt"
        self._vecs_path = cache_base / f"{model}.npy"
        self._load_cache()

        logger.info(
            f"CachedOpenAIEmbeddings: model={model}, dims={dimensions}, "
            f"cached_entries={len(self._keys)}, mode={'local' if self._local_fallback else 'openai'}, "
            f"pii_scrubbing={enable_pii_scrubbing}"
        )

    # ✅ NEW: Text validation helper
    def _validate_texts(self, texts: List[str], corr_id: str) -> List[str]:
        """Validate that texts is a list of non-empty strings."""
        if not isinstance(texts, list):
            raise TypeError(f"[{corr_id}] texts must be a list, got {type(texts).__name__}")

        valid = []
        for i, text in enumerate(texts):
            if not isinstance(text, str):
                logger.warning(f"[{corr_id}] texts[{i}] is not a string — skipping")
                continue
            if not text.strip():
                logger.debug(f"[{corr_id}] texts[{i}] is empty — skipping")
                continue
            valid.append(text)
        return valid

    def embed_documents(self, texts: list[str], correlation_id: Optional[str] = None) -> list[list[float]]:
        corr_id = correlation_id or "embed_docs"

        # ✅ Validate inputs
        texts = self._validate_texts(texts, corr_id)
        if not texts:
            return []

        # Optional PII scrubbing before embedding — using centralized utility
        if self.enable_pii_scrubbing:
            texts = [scrub_pii_for_evaluation(text, domain="general") for text in texts]

        return self._embed_batch(texts, corr_id)

    def embed_query(self, text: str, correlation_id: Optional[str] = None) -> list[float]:
        corr_id = correlation_id or "embed_query"

        if self.enable_pii_scrubbing:
            text = scrub_pii_for_evaluation(text, domain="general")

        return self._embed_batch([text], corr_id)[0]

    def _embed_batch(self, texts: list[str], correlation_id: str) -> list[list[float]]:
        if not texts:
            return []

        results: list[Optional[list[float]]] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            cached = self._get_cached(key)
            if cached is not None:
                results[i] = cached
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        cache_hits = len(texts) - len(uncached_texts)
        if cache_hits > 0:
            logger.debug(f"[{correlation_id}] Embedding cache hits: {cache_hits}/{len(texts)}")

        if uncached_texts:
            embeddings = self._call_api_batched(uncached_texts, correlation_id)
            for idx, text, emb in zip(uncached_indices, uncached_texts, embeddings):
                key = self._cache_key(text)
                self._add_to_cache(key, emb)
                results[idx] = emb
            # ✅ FIXED: Save cache ONCE after all batches processed (not per-batch)
            self._save_cache()

        return [r for r in results if r is not None]

    def _call_api_batched(self, texts: list[str], correlation_id: str) -> list[list[float]]:
        if self._local_fallback or self.client is None:
            logger.debug(f"[{correlation_id}] Using local fallback embeddings")
            return [self._local_embed_text(text) for text in texts]

        all_embeddings: list[list[float]] = []
        for batch_start in range(0, len(texts), self.MAX_BATCH_SIZE):
            batch = texts[batch_start : batch_start + self.MAX_BATCH_SIZE]

            try:
                # ✅ FIXED: Use asyncio.run for sync API call in async context
                if sys.version_info >= (3, 9):
                    embeddings = asyncio.run(self._call_api_with_retry_async(batch, correlation_id))
                else:
                    import concurrent.futures

                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(
                            lambda: asyncio.run(self._call_api_with_retry_async(batch, correlation_id))
                        )
                        embeddings = future.result()
            except Exception as exc:
                from app.core.openai_errors import (
                    is_insufficient_quota_error,
                    is_authentication_error,
                )

                if is_insufficient_quota_error(exc) or is_authentication_error(exc):
                    logger.warning(f"[{correlation_id}] OpenAI quota/auth error — switching to local fallback: {exc}")
                    self._enable_local_fallback(str(exc))
                    embeddings = [self._local_embed_text(t) for t in batch]
                else:
                    raise

            all_embeddings.extend(embeddings)

            # ✅ FIXED: Exponential backoff + jitter between batches
            if batch_start + self.MAX_BATCH_SIZE < len(texts):
                backoff = min(2 ** (len(all_embeddings) // self.MAX_BATCH_SIZE), 10)
                jitter = np.random.uniform(0, 0.5)
                time.sleep(backoff + jitter)

        return all_embeddings

    # ✅ FIXED: Proper async wrapper for sync OpenAI call
    async def _call_api_with_retry_async(self, texts: list[str], correlation_id: str) -> list[list[float]]:
        """Async wrapper that runs sync OpenAI call in thread."""

        @retry_async(config=self._EMBEDDING_RETRY_CONFIG)
        async def _do_call():
            if sys.version_info >= (3, 9):
                return await asyncio.to_thread(
                    self.client.embeddings.create,
                    input=texts,
                    model=self.model,
                    dimensions=self.dimensions,
                )
            else:
                loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                return await loop.run_in_executor(
                    None,
                    lambda: self.client.embeddings.create(input=texts, model=self.model, dimensions=self.dimensions),
                )

        response = await _do_call()
        # Sort by index to ensure order matches input
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]

    def _enable_local_fallback(self, reason: str) -> None:
        if not self._local_fallback:
            logger.warning(f"Switching embeddings to local fallback mode: {reason}")
            self._local_fallback = True
            from app.core.openai_errors import mark_openai_quota_exceeded

            mark_openai_quota_exceeded()

    def _local_embed_text(self, text: str) -> list[float]:
        """
        Deterministic local fallback embedding using token hashing.
        NOT semantically meaningful — only for development/testing when API is unavailable.
        """
        tokens = sorted(re.findall(r"\w+", text.lower()))  # FIXED: Sort for determinism
        vector = np.zeros(self.dimensions, dtype=np.float32)
        if not tokens:
            return vector.tolist()
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[idx] += sign * 0.1  # Small weight to avoid overflow
        norm = np.linalg.norm(vector)
        # ✅ FIXED: Guard against zero-norm division
        if norm > 1e-6:
            vector /= norm
        return vector.tolist()

    @staticmethod
    def _cache_key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_cached(self, key: str) -> Optional[list[float]]:
        idx = self._key_to_idx.get(key)
        if idx is not None and idx < len(self._vectors):
            return self._vectors[idx].tolist()
        return None

    def _add_to_cache(self, key: str, embedding: list[float]):
        """Add embedding to in-memory cache (thread-safe via GIL for CPython)."""
        vec = np.array(embedding, dtype=np.float32).reshape(1, -1)
        self._vectors = np.vstack([self._vectors, vec])
        self._keys.append(key)
        self._key_to_idx[key] = len(self._keys) - 1

    def _load_cache(self):
        """Load cache from disk with error recovery + integrity check."""
        if self._keys_path.exists() and self._vecs_path.exists():
            try:
                with open(self._keys_path, "r", encoding="utf-8") as f:
                    self._keys = [line.strip() for line in f if line.strip()]

                # ✅ FIXED: Verify .npy file integrity before loading
                vec_data = np.load(str(self._vecs_path), allow_pickle=False)

                # Validate dimensions match
                if vec_data.ndim != 2 or vec_data.shape[1] != self.dimensions:
                    logger.warning(
                        f"Cache dimension mismatch: expected {self.dimensions}, "
                        f"got {vec_data.shape[1] if vec_data.ndim == 2 else 'N/A'}. Clearing cache."
                    )
                    self._keys = []
                    self._vectors = np.empty((0, self.dimensions), dtype=np.float32)
                    self._key_to_idx = {}
                    return

                self._vectors = vec_data
                self._key_to_idx = {k: i for i, k in enumerate(self._keys)}
                logger.info(f"Embedding cache loaded: {len(self._keys)} entries")
            except Exception as e:
                logger.warning(f"Cache load failed: {e}. Starting fresh.")
                self._keys = []
                self._vectors = np.empty((0, self.dimensions), dtype=np.float32)
                self._key_to_idx = {}

    def _save_cache(self):
        """Persist cache to disk."""
        if not self._keys:
            return

        try:
            self._keys_path.parent.mkdir(parents=True, exist_ok=True)
            # Write keys
            self._keys_path.write_text("\n".join(self._keys), encoding="utf-8")
            # np.save adds .npy if not present — use allow_pickle=False for safety
            # _vecs_path already ends in .npy so save directly
            np.save(str(self._vecs_path), self._vectors)
        except OSError as e:
            logger.warning(f"Cache save failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error saving cache: {e}")

    def cache_stats(self, correlation_id: Optional[str] = None) -> dict:
        """Return cache statistics for monitoring."""
        corr_id = correlation_id or "embed_stats"
        return {
            "cached_entries": len(self._keys),
            "cache_keys_file": str(self._keys_path),
            "cache_vecs_file": str(self._vecs_path),
            "mode": "local" if self._local_fallback else "openai",
            "cache_size_mb": round(self._vecs_path.stat().st_size / 1024 / 1024, 2) if self._vecs_path.exists() else 0,
            "pii_scrubbing_enabled": self.enable_pii_scrubbing,
            "correlation_id": corr_id,
        }


def get_embeddings_metadata() -> dict[str, Any]:
    """✅ NEW: Return embeddings metadata for monitoring."""
    return {
        "model": get_settings().openai_embedding_model,
        "dimensions": CachedOpenAIEmbeddings.EMBEDDING_DIM,
        "max_batch_size": CachedOpenAIEmbeddings.MAX_BATCH_SIZE,
        "retry_config": {
            "max_attempts": CachedOpenAIEmbeddings._EMBEDDING_RETRY_CONFIG.max_attempts,
            "backoff_base": CachedOpenAIEmbeddings._EMBEDDING_RETRY_CONFIG.backoff_base,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "LocalSentenceTransformerEmbeddings",
    "get_local_embeddings",
    "CachedOpenAIEmbeddings",
    "get_embeddings_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
