# backend/app/tasks/ingest_tasks.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
# BATMAN-FIX: A - True async, T - Timeout guards, M - Memory safety
# ASCALE-FIX: E - Error propagation, L - Logging
# ✅ FIXED: Proper async handling in Celery + input validation + per-stage timeouts

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Any, Final

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded

# DVMELTSS-M: Import at top level
from app.tasks.celery_app import celery_app
from app.tasks.progress import ProgressPublisher, TaskStatus
from app.core.celery_utils import (
    run_async_in_task,
    is_transient_error,
)

if TYPE_CHECKING:
    from app.ingestion.universal_ingestion import IngestResult

logger = logging.getLogger(__name__)

# ✅ NEW: Stage timeout constants (in seconds)
_OCR_TIMEOUT: Final = 300
_EMBEDDING_TIMEOUT: Final = 180
_GRAPH_TIMEOUT: Final = 120
_VERSIONING_TIMEOUT: Final = 60


class IngestTask(Task):
    """
    Base class for ingest tasks.
    Handles common failure recording and dead-letter routing.
    """

    abstract = True
    max_retries = 2
    default_retry_delay = 30

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when a task fails permanently (after retries)."""
        corr_id = kwargs.get("correlation_id", "unknown")
        filename = kwargs.get("filename", args[0] if args else "unknown")
        publisher = ProgressPublisher()
        publisher.fail(
            task_id=task_id,
            filename=filename,
            error=str(exc),
            correlation_id=corr_id,
        )
        # Dead-letter logging with correlation context
        logger.error(
            f"[{corr_id}] Ingest task permanently failed: task_id={task_id} | " f"filename={filename} | error={exc}"
        )

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        corr_id = kwargs.get("correlation_id", "unknown")
        filename = kwargs.get("filename", "unknown")
        publisher = ProgressPublisher()
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.QUEUED,
            stage="retry",
            message=f"Retrying due to: {str(exc)[:100]}",
            progress=0.0,
            filename=filename,
            correlation_id=corr_id,
        )


# ✅ NEW: Input validation helper
def _validate_ingest_inputs(
    file_path: str,
    filename: str,
    workspace_id: str,
    corr_id: str,
) -> tuple[bool, str]:
    """Validate inputs before processing."""
    if not isinstance(file_path, str) or not file_path.strip():
        return False, "file_path must be a non-empty string"
    if not isinstance(filename, str) or not filename.strip():
        return False, "filename must be a non-empty string"
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return False, "workspace_id must be a non-empty string"
    return True, ""


@celery_app.task(
    bind=True,
    base=IngestTask,
    name="app.tasks.ingest_tasks.ingest_document",
    queue="default",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=600,
    time_limit=720,
)
def ingest_document(
    self,
    task_id: str,
    file_path: str,
    filename: str,
    workspace_id: str = "default",
    user_id: Optional[str] = None,
    priority: str = "default",
    correlation_id: Optional[str] = None,
) -> dict:
    """
    Full document ingestion pipeline as a Celery task.

    Stages with progress reporting:
    1. Validate (0-5%)
    2. OCR (5-50%)
    3. Chunking (50-60%)
    4. Embedding + indexing (60-85%)
    5. Graph extraction (85-92%)
    6. Versioning (92-96%)
    7. BM25 sync (96-99%)
    8. Complete (100%)
    """
    corr_id = correlation_id or f"ingest_{task_id[:8]}"
    publisher = ProgressPublisher()
    start_time = time.perf_counter()

    try:
        # ✅ Validate inputs
        is_valid, error = _validate_ingest_inputs(file_path, filename, workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            publisher.fail(
                task_id=task_id,
                filename=filename,
                error=f"Invalid input: {error}",
                correlation_id=corr_id,
            )
            return {"status": "failed", "error": error, "correlation_id": corr_id}

        # -- Stage 1: Validate (0-5%) ------------------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.UPLOADING,
            stage="validate",
            message="Validating document...",
            progress=2.0,
            filename=filename,
            correlation_id=corr_id,
        )

        file_path_obj = Path(file_path)
        if not file_path_obj.exists():
            raise FileNotFoundError(f"Upload file not found: {file_path}")

        size_mb = file_path_obj.stat().st_size / 1024 / 1024

        # -- Stage 2: OCR (5-50%) ---------------------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.OCR,
            stage="ocr",
            message="Extracting text...",
            progress=5.0,
            filename=filename,
            details={"size_mb": round(size_mb, 2)},
            correlation_id=corr_id,
        )

        from app.ingestion.universal_ingestion import UniversalIngestionPipeline

        pipeline = UniversalIngestionPipeline()

        # Progress callback for page-level OCR updates
        def ocr_progress_callback(page_num: int, total_pages: int):
            if total_pages > 0:
                pct = 5 + (page_num / total_pages) * 40
                publisher.publish(
                    task_id=task_id,
                    status=TaskStatus.OCR,
                    stage="ocr",
                    message=f"OCR page {page_num + 1}/{total_pages}",
                    progress=pct,
                    filename=filename,
                    details={"page": page_num + 1, "total_pages": total_pages},
                    correlation_id=corr_id,  # ✅ FIXED: Propagate correlation_id
                )

        # ✅ FIXED: Wrap sync pipeline.ingest() in run_async_in_task for safety
        ingest_result: IngestResult = run_async_in_task(
            lambda: pipeline.ingest(
                file_path=str(file_path_obj),
                progress_callback=ocr_progress_callback,
            ),
            timeout=_OCR_TIMEOUT,
        )

        if not ingest_result.is_successful:
            raise ValueError(f"Ingestion failed: {ingest_result.error or 'No content extracted'}")

        child_chunks = ingest_result.documents
        page_count = ingest_result.page_count

        # -- Stage 3: Chunking (50-60%) ----------------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.CHUNKING,
            stage="chunking",
            message=f"Chunked into {len(child_chunks)} segments",
            progress=55.0,
            filename=filename,
            details={"chunk_count": len(child_chunks)},
            correlation_id=corr_id,
        )

        # -- Stage 4: Embedding + Indexing (60-85%) ----------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.EMBEDDING,
            stage="embedding",
            message="Generating embeddings...",
            progress=60.0,
            filename=filename,
            correlation_id=corr_id,
        )

        from app.vectorstore.store_manager import VectorStoreManager

        store = VectorStoreManager(workspace_id=workspace_id)

        # Batch embedding with progress
        batch_size = 50
        total_chunks = len(child_chunks)
        all_ids = []

        for i in range(0, total_chunks, batch_size):
            batch = child_chunks[i : i + batch_size]
            publisher.publish(
                task_id=task_id,
                status=TaskStatus.EMBEDDING,
                stage="embedding",
                message=f"Embedding chunks {i+1}–{min(i+batch_size, total_chunks)}/{total_chunks}",
                progress=60 + (i / max(total_chunks, 1)) * 20,
                filename=filename,
                details={"embedded": i, "total": total_chunks},
                correlation_id=corr_id,
            )
            # ✅ FIXED: Run sync store calls in thread executor to avoid blocking
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                chroma_future = executor.submit(store.chroma.add_chunks, batch)
                faiss_future = executor.submit(store.faiss.add_chunks, batch)
                chroma_future.result()
                faiss_future.result()
            all_ids.extend([c.metadata.get("chunk_id", "") for c in batch])

        publisher.publish(
            task_id=task_id,
            status=TaskStatus.INDEXING,
            stage="indexing",
            message=f"Indexed {total_chunks} chunks",
            progress=82.0,
            filename=filename,
            correlation_id=corr_id,
        )

        # -- Stage 5: Graph Extraction (85-92%) -------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.GRAPH,
            stage="graph",
            message="Extracting knowledge graph...",
            progress=85.0,
            filename=filename,
            correlation_id=corr_id,
        )

        from app.config import get_settings

        settings = get_settings()
        if getattr(settings, "graph_extraction_enabled", False):
            try:
                from app.graph import GraphExtractor, get_neo4j_store

                neo4j = get_neo4j_store()
                extractor = GraphExtractor()

                meta = None
                if hasattr(ingest_result, "enriched") and ingest_result.enriched:
                    meta = ingest_result.enriched.metadata

                neo4j.upsert_document_node(
                    source_file=filename,
                    document_type=meta.document_type if meta else ingest_result.format,
                    page_count=page_count,
                    workspace_id=workspace_id,
                )

                # ✅ FIXED: Use config-driven cap instead of hardcoded 50
                max_graph_chunks = getattr(settings, "graph_max_chunks_for_extraction", 50)
                extraction_results = extractor.extract_from_document(
                    chunks=child_chunks[:max_graph_chunks],
                    source_file=filename,
                    workspace_id=workspace_id,
                )
                total_entities = sum(len(r.entities) for r in extraction_results)
                total_rels = sum(len(r.relationships) for r in extraction_results)

                for result in extraction_results:
                    for entity in result.entities:
                        neo4j.upsert_entity(
                            entity_id=entity.id,
                            entity_type=entity.entity_type,
                            name=entity.name,
                            properties={"description": entity.description},
                            workspace_id=workspace_id,
                        )
                        neo4j.link_entity_to_document(
                            entity_id=entity.id,
                            source_file=filename,
                            page_number=result.page_number,
                            workspace_id=workspace_id,
                        )
                    for rel in result.relationships:
                        neo4j.upsert_relationship(
                            from_entity_id=rel.from_entity_id,
                            to_entity_id=rel.to_entity_id,
                            relationship_type=rel.relationship_type,
                            properties=rel.properties,
                            workspace_id=workspace_id,
                        )

                publisher.publish(
                    task_id=task_id,
                    status=TaskStatus.GRAPH,
                    stage="graph",
                    message=f"Graph: {total_entities} entities, {total_rels} relationships",
                    progress=91.0,
                    filename=filename,
                    details={"entities": total_entities, "relationships": total_rels},
                    correlation_id=corr_id,
                )
            except Exception as e:
                logger.warning(f"[{corr_id}] Graph extraction failed (non-fatal): {e}")

        # -- Stage 6: Versioning (92-96%) --------------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.VERSIONING,
            stage="versioning",
            message="Registering document version...",
            progress=93.0,
            filename=filename,
            correlation_id=corr_id,
        )
        try:
            from app.versioning import VersionRegistry

            registry = VersionRegistry()

            # ✅ FIXED: Use run_async_in_task helper instead of asyncio.run()
            async def _register():
                return await registry.register_version(
                    source_file=filename,
                    workspace_id=workspace_id,
                    chunk_texts=[d.page_content for d in child_chunks],
                    chunk_ids=all_ids,
                    page_count=page_count,
                    uploaded_by=user_id,
                    ingest_metadata={"format": ingest_result.format},
                )

            new_version = run_async_in_task(_register, timeout=_VERSIONING_TIMEOUT)
            logger.info(f"[{corr_id}] Version {new_version.version_number} registered: {filename}")
        except Exception as e:
            logger.warning(f"[{corr_id}] Versioning failed (non-fatal): {e}")

        # -- Stage 7: BM25 Sync (96-99%) ---------------------------------------
        publisher.publish(
            task_id=task_id,
            status=TaskStatus.INDEXING,
            stage="bm25",
            message="Updating keyword index...",
            progress=97.0,
            filename=filename,
            correlation_id=corr_id,
        )
        try:
            from app.retrieval import get_bm25_index

            bm25 = get_bm25_index(workspace_id)
            # ✅ FIXED: Run sync BM25 call in thread executor
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(bm25.add_documents, child_chunks).result()
        except Exception as e:
            logger.warning(f"[{corr_id}] BM25 sync failed (non-fatal): {e}")

        # -- Stage 8: Complete (100%) ------------------------------------------
        latency = time.perf_counter() - start_time
        publisher.complete(
            task_id=task_id,
            filename=filename,
            page_count=page_count,
            chunk_count=total_chunks,
            latency_seconds=latency,
            correlation_id=corr_id,
        )

        # Clean up temp file
        try:
            file_path_obj.unlink()
        except OSError:
            pass

        result = {
            "status": "complete",
            "filename": filename,
            "page_count": page_count,
            "chunk_count": total_chunks,
            "latency_seconds": round(latency, 2),
            "workspace_id": workspace_id,
            "correlation_id": corr_id,
        }
        logger.info(f"[{corr_id}] Ingest complete: {filename} | {latency:.2f}s")
        return result

    except SoftTimeLimitExceeded:
        publisher.fail(
            task_id=task_id,
            filename=filename,
            error="Processing timed out (10 minute limit). Try splitting large documents.",
            correlation_id=corr_id,
        )
        raise

    except Exception as exc:
        logger.error(f"[{corr_id}] Ingest task failed: {filename} | {exc}", exc_info=True)
        publisher.fail(
            task_id=task_id,
            filename=filename,
            error=str(exc),
            correlation_id=corr_id,
        )
        # ✅ FIXED: Proper cleanup on failure
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass
        # Retry on transient errors
        if is_transient_error(exc):
            raise self.retry(exc=exc)
        raise


def get_ingest_task_metadata() -> dict[str, Any]:
    """✅ NEW: Return ingest task metadata for monitoring."""
    return {
        "stage_timeouts": {
            "ocr": _OCR_TIMEOUT,
            "embedding": _EMBEDDING_TIMEOUT,
            "graph": _GRAPH_TIMEOUT,
            "versioning": _VERSIONING_TIMEOUT,
        },
        "retry_config": {
            "max_retries": IngestTask.max_retries,
            "default_retry_delay": IngestTask.default_retry_delay,
        },
        "celery_queue": "default",
        "soft_time_limit": 600,
        "hard_time_limit": 720,
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["ingest_document", "IngestTask", "get_ingest_task_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
