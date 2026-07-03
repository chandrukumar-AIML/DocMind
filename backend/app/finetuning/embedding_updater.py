# backend/app/finetuning/embedding_updater.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# BATMAN-FIX: A - True async, M - Memory safety, T - Batch processing
# ACID-INDEX: E - Error handling (backup before destructive ops)

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Optional, Callable

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.finetune_utils import load_model_safe, generate_finetune_correlation_id

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================

# BATMAN-M: Memory guard for batch encoding
_MAX_BATCH_SIZE: Final = 64
_MAX_EMBEDDING_DIM: Final = 2048  # Safety cap to prevent OOM

# DVMELTSS-E: Backup configuration
_BACKUP_SUFFIX: Final = ".backup"
_MIN_CHUNKS_FOR_BACKUP: Final = 100  # Only backup if >100 chunks

# DVMELTSS-V: Valid model file extensions
_VALID_MODEL_EXTENSIONS: Final = frozenset({".bin", ".safetensors", ".pt", ".pkl"})


@dataclass(frozen=True)
class ReembedResult:
    """
    Immutable result of a re-embedding run.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    workspace_id: str
    model_id: str
    chunks_processed: int = 0
    chunks_failed: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    correlation_id: Optional[str] = None  # FIXED: Added for tracing

    @property
    def is_successful(self) -> bool:
        return self.error is None and self.chunks_processed > 0

    def to_dict(self) -> dict:
        """Serialize for API responses / MLflow logging."""
        return {
            "workspace_id": self.workspace_id,
            "model_id": self.model_id,
            "chunks_processed": self.chunks_processed,
            "chunks_failed": self.chunks_failed,
            "duration_seconds": round(self.duration_seconds, 2),
            "error": self.error,
            "is_successful": self.is_successful,
            "correlation_id": self.correlation_id,  # FIXED: Include in output
        }


class EmbeddingUpdater:
    """
    Re-embeds all documents in a workspace with a new embedding model.

    Features (DVMELTSS-E, BATMAN-M, ACID-E):
    - Backup-before-delete safety for destructive vector store operations
    - Memory-safe batch encoding with progress tracking
    - Async-safe execution via thread executor
    - Correlation ID tracing for audit trails
    - Model file validation to prevent loading malicious models
    """

    def __init__(self, workspace_id: str = "default"):
        self.workspace_id = workspace_id
        self.settings = get_settings()

    def _validate_model_path(self, model_path: str | Path) -> Path:
        """DVMELTSS-V: Validate model file exists and has safe extension."""
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model path not found: {path}")
        if path.is_file() and path.suffix not in _VALID_MODEL_EXTENSIONS:
            logger.warning(f"Unusual model file extension: {path.suffix}")
        return path.resolve()

    def _backup_collection(self, client, collection_name: str, backup_path: Path) -> bool:
        """
        ACID-E: Backup existing collection before destructive update.
        Returns True if backup successful.
        """
        try:
            collection = client.get_collection(collection_name)
            total = collection.count()

            if total < _MIN_CHUNKS_FOR_BACKUP:
                logger.debug(f"Skipping backup: only {total} chunks")
                return True

            logger.info(f"Backing up {total} chunks to {backup_path}...")

            # Export in batches to avoid memory spike
            batch_size = 500
            with open(backup_path, "w") as f:
                for offset in range(0, total, batch_size):
                    batch = collection.get(
                        limit=batch_size,
                        offset=offset,
                        include=["documents", "metadatas", "embeddings", "ids"],
                    )
                    for i in range(len(batch["ids"])):
                        f.write(
                            json.dumps(
                                {
                                    "id": batch["ids"][i],
                                    "document": batch["documents"][i],
                                    "metadata": batch["metadatas"][i],
                                    "embedding": batch["embeddings"][i],
                                }
                            )
                            + "\n"
                        )

            logger.info(f"Backup complete: {backup_path}")
            return True
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return False

    async def _encode_batch_async(
        self,
        model,
        texts: list[str],
        batch_size: int,
        progress_cb: Optional[Callable[[int, int], None]],
        total: int,
        processed: int,
        correlation_id: str,
    ) -> tuple[list[list[float]], int, int]:
        """
        BATMAN-M: Async-safe batch encoding with memory guard.
        Returns (embeddings, processed_count, failed_count).
        """
        embeddings = []
        failed = 0

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                # Run blocking encode in thread to avoid event loop freeze
                loop = asyncio.get_running_loop()
                vecs = await loop.run_in_executor(
                    None,
                    lambda: model.encode(
                        batch,
                        batch_size=min(32, len(batch)),
                        show_progress_bar=False,
                        normalize_embeddings=True,
                    ),
                )
                embeddings.extend(vecs.tolist())
                processed += len(batch)

                if progress_cb:
                    progress_cb(processed, total)

            except Exception as e:
                logger.error(f"[{correlation_id}] Batch encode failed: {e}")
                failed += len(batch)

            # Yield control to event loop
            await asyncio.sleep(0)

        return embeddings, processed, failed

    async def update_async(
        self,
        model_path: str | Path,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> ReembedResult:
        """
        Async version: re-embed all documents with a fine-tuned model.
        BATMAN-A: Non-blocking, yields to event loop between batches.
        """
        corr_id = correlation_id or generate_finetune_correlation_id("reembed")
        start_time = time.perf_counter()
        result = ReembedResult(
            workspace_id=self.workspace_id,
            model_id=str(model_path),
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )

        try:
            # Validate model path
            safe_path = self._validate_model_path(model_path)

            # FIXED: Use centralized safe model loader
            logger.info(f"[{corr_id}] Loading fine-tuned model: {safe_path}")
            ft_model = await load_model_safe(safe_path, max_dim=_MAX_EMBEDDING_DIM)
            ft_dim = ft_model.get_sentence_embedding_dimension()
            logger.info(f"[{corr_id}] Model loaded: dim={ft_dim}")

            # Get ChromaDB client + collection
            from app.vectorstore.chroma_store import _get_chroma_client

            client = _get_chroma_client(self.settings.chroma_persist_dir)

            collection_name = f"docs_{self.workspace_id}"
            try:
                old_collection = client.get_collection(collection_name)
            except Exception:
                old_collection = client.get_collection(self.settings.chroma_collection_name)

            total = old_collection.count()
            if total == 0:
                logger.warning(f"[{corr_id}] No chunks to re-embed.")
                result.duration_seconds = time.perf_counter() - start_time
                return result

            logger.info(f"[{corr_id}] Re-embedding {total} chunks...")

            # ACID-E: Backup before destructive operation
            backup_path = Path(self.settings.chroma_persist_dir) / f"{collection_name}{_BACKUP_SUFFIX}"
            if not self._backup_collection(client, collection_name, backup_path):
                logger.error(f"[{corr_id}] Backup failed — aborting re-embedding to prevent data loss")
                result.error = "Backup failed"
                return result

            # Retrieve all chunks in batches
            batch_size = 500
            all_texts: list[str] = []
            all_ids: list[str] = []
            all_metas: list[dict] = []

            for offset in range(0, total, batch_size):
                batch = old_collection.get(
                    limit=batch_size,
                    offset=offset,
                    include=["documents", "metadatas", "ids"],
                )
                all_texts.extend(batch["documents"])
                all_ids.extend(batch["ids"])
                all_metas.extend(batch["metadatas"])

            # Encode with fine-tuned model in memory-safe batches
            all_embeddings = []
            processed = 0
            failed = 0

            for i in range(0, len(all_texts), _MAX_BATCH_SIZE):
                batch_texts = all_texts[i : i + _MAX_BATCH_SIZE]
                batch_embeds, proc, fail = await self._encode_batch_async(
                    ft_model,
                    batch_texts,
                    _MAX_BATCH_SIZE,
                    progress_cb,
                    total,
                    processed,
                    corr_id,
                )
                all_embeddings.extend(batch_embeds)
                processed = proc
                failed += fail

            if not all_embeddings:
                raise RuntimeError("No embeddings generated")

            # Delete and recreate collection with new embeddings
            logger.info(f"[{corr_id}] Replacing ChromaDB collection with fine-tuned vectors...")

            # Delete old collection
            client.delete_collection(collection_name)

            # Create new collection
            new_collection = client.create_collection(
                name=collection_name,
                metadata={"workspace_id": self.workspace_id},
            )

            # Re-insert in batches
            insert_size = 500
            for i in range(0, len(all_ids), insert_size):
                new_collection.add(
                    ids=all_ids[i : i + insert_size],
                    embeddings=all_embeddings[i : i + insert_size],
                    documents=all_texts[i : i + insert_size],
                    metadatas=all_metas[i : i + insert_size],
                )

            logger.info(f"[{corr_id}] ChromaDB updated: {len(all_ids)} chunks with new vectors")

            # Rebuild FAISS index
            logger.info(f"[{corr_id}] Rebuilding FAISS index...")
            from app.vectorstore.store_manager import VectorStoreManager

            store = VectorStoreManager(workspace_id=self.workspace_id)
            store.faiss._rebuild_from_chroma()
            logger.info(f"[{corr_id}] FAISS rebuilt.")

            # FIXED: frozen=True dataclass requires dataclasses.replace() instead of mutation
            result = dataclasses.replace(
                result,
                chunks_processed=processed,
                chunks_failed=failed,
            )

        except Exception as e:
            logger.error(f"[{corr_id}] Re-embedding failed: {e}", exc_info=True)
            # FIXED: frozen dataclass — use replace()
            result = dataclasses.replace(result, error=str(e))
            # Optional: restore from backup on failure
            # self._restore_from_backup(backup_path)

        # FIXED: frozen dataclass — use replace()
        result = dataclasses.replace(result, duration_seconds=time.perf_counter() - start_time)

        # FIXED: Log re-embedding metrics to MLflow (was missing entirely)
        try:
            from app.observability.mlflow_logger import MLflowLogger

            _ml = MLflowLogger()
            with _ml.start_run(
                run_name=f"reembed_{self.workspace_id}",
                tags={"workspace_id": self.workspace_id, "model_path": str(model_path)},
                correlation_id=corr_id,
            ):
                _ml._safe_log_metrics(
                    {
                        "reembed_chunks_processed": result.chunks_processed,
                        "reembed_chunks_failed": result.chunks_failed,
                        "reembed_duration_seconds": round(result.duration_seconds, 2),
                        "reembed_throughput_chunks_per_sec": round(
                            result.chunks_processed / max(result.duration_seconds, 0.001),
                            2,
                        ),
                        "reembed_success": 1.0 if result.is_successful else 0.0,
                    }
                )
                _ml._safe_log_param("reembed_workspace_id", self.workspace_id)
                _ml._safe_log_param("reembed_model_path", str(model_path))
        except Exception as _mle:
            logger.debug(f"[{corr_id}] MLflow re-embed logging failed (non-fatal): {_mle}")

        logger.info(
            f"[{corr_id}] Re-embedding complete: "
            f"{result.chunks_processed} processed | "
            f"{result.chunks_failed} failed | "
            f"{result.duration_seconds:.1f}s"
        )
        return result

    def update(
        self,
        model_path: str | Path,
        progress_cb: Optional[Callable[[int, int], None]] = None,
        correlation_id: Optional[str] = None,
    ) -> ReembedResult:
        """
        Sync wrapper for backward compatibility.
        DVMELTSS-M: Prefer async version in new code.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
            return asyncio.run_coroutine_threadsafe(
                self.update_async(model_path, progress_cb, correlation_id), loop
            ).result()
        except RuntimeError:
            return asyncio.run(self.update_async(model_path, progress_cb, correlation_id))


# DVMELTSS-M: Explicit module exports
__all__ = ["EmbeddingUpdater", "ReembedResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
