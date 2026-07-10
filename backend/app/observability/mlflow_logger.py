
from __future__ import annotations

import asyncio
import csv
import logging
import os
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Optional, Dict, List
from urllib.parse import urlparse

import mlflow

from app.config import get_settings

# DVMELTSS-M: Import centralized utilities
from app.core.pii_utils import scrub_pii_for_evaluation

logger = logging.getLogger(__name__)

# Composite metric keys for RAG evaluation
COMPOSITE_METRICS: Final = [
    "faithfulness",
    "answer_relevance",
    "context_precision",
    "context_recall",
]

_SOCKET_TIMEOUT: Final = 2.0


def configure_mlflow(correlation_id: Optional[str] = None) -> bool:
    """
    Configure MLflow tracking with graceful fallback.

    Behaviors:
    - If remote URI unreachable, fall back to local file store
    - Never raises — all failures logged as warnings
    - Creates local directories as needed
    - FIXED: Accepts correlation_id for distributed tracing

    Args:
        correlation_id: Optional request ID for tracing context

    Returns:
        True if configured successfully, False if using fallback or failed
    """
    corr_id = correlation_id or "mlflow_config"
    settings = get_settings()
    uri = settings.mlflow_tracking_uri

    # For remote URIs: quick reachability check to avoid urllib3 retries blocking startup
    if uri.startswith("http://") or uri.startswith("https://"):
        parsed = urlparse(uri)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        try:
            sock = socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT)
            sock.close()
        except (OSError, socket.timeout) as e:
            fallback_uri = "./data/mlflow"
            logger.warning(
                f"[{corr_id}] MLflow server at {uri} unreachable (timeout {_SOCKET_TIMEOUT}s). "
                f"Falling back to local file store: {fallback_uri} | error={e}"
            )
            uri = fallback_uri
        except Exception as e:
            logger.warning(f"[{corr_id}] Socket check failed: {e}. Using default URI.")

    try:
        # Ensure local directory exists for file-based store
        if not (uri.startswith("http://") or uri.startswith("https://")):
            Path(uri).mkdir(parents=True, exist_ok=True)

        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(settings.mlflow_experiment_name)

        logger.info(f"[{corr_id}] MLflow configured: uri={uri}, " f"experiment={settings.mlflow_experiment_name}")
        return True
    except Exception as e:
        logger.warning(f"[{corr_id}] MLflow configuration failed: {e}. Continuing without tracking.")
        return False


@dataclass
class _NullRun:
    """Sentinel returned when MLflow run fails to start."""

    info: Any = None
    is_null: bool = True


NULL_RUN = _NullRun()


class MLflowLogger:
    """
    Domain-aware MLflow logging wrapper for DocuMind AI.

    Features:
    - Circuit breaker pattern to disable after repeated failures
    - Auto-detect nested runs for hierarchical experiment tracking
    - Domain-specific logging methods (OCR, retrieval, RAG, ingestion)
    - Async support for non-blocking artifact logging
    - Timer context manager for easy latency tracking
    - Correlation ID tracing for distributed debugging
    """

    # Class-level circuit breaker state
    _mlflow_available: bool = True
    _failure_count: int = 0
    _FAILURE_THRESHOLD: Final = 3
    _class_lock = threading.Lock()  # Thread-safe circuit breaker

    def __init__(self, experiment_name: Optional[str] = None):
        settings = get_settings()
        self.experiment_name = experiment_name or settings.mlflow_experiment_name
        self._active_run = None

        try:
            mlflow.set_experiment(self.experiment_name)
        except Exception as e:
            logger.warning(f"Could not set MLflow experiment '{self.experiment_name}': {e}")

    @contextmanager
    def start_run(
        self,
        run_name: str,
        tags: Optional[Dict[str, str]] = None,
        nested: Optional[bool] = None,
        correlation_id: Optional[str] = None,
    ):
        """
        Context manager for MLflow run with auto-nesting detection.

        Args:
            run_name: Human-readable name for the run
            tags: Optional dict of tags for filtering in UI
            nested: If None, auto-detect based on active run; else force behavior
            correlation_id: Request ID for distributed tracing

        Yields:
            MLflow run object or NULL_RUN sentinel on failure
        """
        corr_id = correlation_id or "mlflow_run"
        settings = get_settings()

        # Auto-detect nesting: if a run is already active, nest by default
        active_run = mlflow.active_run()
        use_nested = nested if nested is not None else (active_run is not None)

        # Merge default tags with user-provided + correlation_id
        default_tags = {
            "app": "documind-ai",
            "version": settings.app_version,
            "environment": "production" if not settings.api_reload else "development",
            **({"correlation_id": corr_id} if corr_id else {}),
        }
        if tags:
            default_tags.update({k: scrub_pii_for_evaluation(str(v)[:500], domain="general") for k, v in tags.items()})

        run = None
        try:
            run = mlflow.start_run(
                run_name=run_name,
                nested=use_nested,
                tags=default_tags,
            )
            self._active_run = run
            ui_base = settings.mlflow_tracking_uri.rstrip("/")
            if ui_base.startswith("http"):
                ui_url = f"{ui_base}/#/experiments/{run.info.experiment_id}" f"/runs/{run.info.run_id}"
            else:
                # For file-based URIs, use resolved path
                file_uri = Path(settings.mlflow_tracking_uri).resolve()
                ui_url = f"file://{file_uri}/#/experiments/{run.info.experiment_id}/runs/{run.info.run_id}"

            logger.info(f"[{corr_id}] MLflow run: '{run_name}' | id={run.info.run_id} | ui={ui_url}")
            yield run
        except Exception as e:
            logger.warning(f"[{corr_id}] MLflow run '{run_name}' failed: {e}")
            yield NULL_RUN
        finally:
            if run is not None:
                try:
                    mlflow.end_run()
                except Exception as end_err:
                    logger.debug(f"Failed to end MLflow run: {end_err}")
            self._active_run = None

    def log_ocr_metrics(
        self,
        cer: float,
        wer: float,
        mean_confidence: float,
        fallback_rate: float,
        pages_processed: int,
        latency_seconds: float,
        source_file: Optional[str] = None,
        step: Optional[int] = None,
        correlation_id: Optional[str] = None,
    ):
        """Log OCR evaluation metrics to MLflow."""
        cer = max(0.0, min(1.0, cer))
        wer = max(0.0, min(1.0, wer))
        mean_confidence = max(0.0, min(1.0, mean_confidence))
        fallback_rate = max(0.0, min(1.0, fallback_rate))
        latency_seconds = max(0.0, latency_seconds)
        pages_processed = max(0, pages_processed)

        metrics = {
            "ocr_mean_cer": round(cer, 6),
            "ocr_mean_wer": round(wer, 6),
            "ocr_accuracy_cer": round(1 - cer, 6),
            "ocr_accuracy_wer": round(1 - wer, 6),
            "ocr_mean_confidence": round(mean_confidence, 6),
            "ocr_fallback_rate": round(fallback_rate, 6),
            "ocr_pages_processed": pages_processed,
            "ocr_latency_seconds": round(latency_seconds, 3),
            "ocr_pages_per_second": round(pages_processed / max(latency_seconds, 0.001), 3),
        }
        self._safe_log_metrics(metrics, step=step)
        if source_file:
            self._safe_log_param("ocr_source_file", os.path.basename(source_file))

    def _safe_log_metrics(self, metrics: Dict[str, Any], step: Optional[int] = None):
        """
        Log metrics with circuit breaker protection.

        Disables MLflow after _FAILURE_THRESHOLD consecutive failures.
        Thread-safe via class-level lock.
        """
        if not self.__class__._mlflow_available:
            return

        with self.__class__._class_lock:
            try:
                mlflow.log_metrics(metrics, step=step)
                # Reset failure count on success
                self.__class__._failure_count = 0
            except Exception as e:
                self.__class__._failure_count += 1
                if self.__class__._failure_count >= self.__class__._FAILURE_THRESHOLD:
                    self.__class__._mlflow_available = False
                    logger.warning(f"MLflow disabled after {self.__class__._FAILURE_THRESHOLD} consecutive failures.")
                else:
                    logger.debug("MLflow log_metrics failed: %s", e)

    def log_ocr_params(
        self,
        ocr_engine: str = "paddleocr",
        languages: Optional[List[str]] = None,
        use_gpu: bool = False,
        confidence_threshold: float = 0.85,
        enable_layout: bool = True,
        deskew: bool = True,
        denoise: bool = True,
    ):
        """Log OCR configuration parameters."""
        self._safe_log_params(
            {
                "ocr_engine": ocr_engine,
                "ocr_languages": ",".join(languages or ["en"]),
                "ocr_use_gpu": str(use_gpu),
                "ocr_confidence_threshold": str(confidence_threshold),
                "ocr_enable_layout": str(enable_layout),
                "ocr_preprocessing_deskew": str(deskew),
                "ocr_preprocessing_denoise": str(denoise),
            }
        )

    def log_chunking_params(
        self,
        strategy: str,
        child_chunk_size: int,
        parent_chunk_size: int,
        child_overlap: int,
        parent_overlap: int,
        total_chunks: int,
        total_documents: int,
    ):
        """Log chunking strategy parameters and derived metrics."""
        self._safe_log_params(
            {
                "chunking_strategy": strategy,
                "chunking_child_size": str(child_chunk_size),
                "chunking_parent_size": str(parent_chunk_size),
                "chunking_child_overlap": str(child_overlap),
                "chunking_parent_overlap": str(parent_overlap),
            }
        )
        self._safe_log_metrics(
            {
                "chunking_total_chunks": total_chunks,
                "chunking_total_documents": total_documents,
                "chunking_avg_chunks_per_doc": round(total_chunks / max(total_documents, 1), 2),
            }
        )

    def log_retrieval_metrics(
        self,
        precision_at_k: float,
        recall_at_k: float,
        mrr: float,
        hit_rate: float,
        k: int,
        n_queries: int,
        mean_latency_ms: float,
        step: Optional[int] = None,
    ):
        """
        Log retrieval evaluation metrics.

        Note: Metric names are consistent regardless of k value for easy aggregation.
        """
        # ✅ Clamp metrics to valid ranges
        precision_at_k = max(0.0, min(1.0, precision_at_k))
        recall_at_k = max(0.0, min(1.0, recall_at_k))
        mrr = max(0.0, min(1.0, mrr))
        hit_rate = max(0.0, min(1.0, hit_rate))

        metrics = {
            "retrieval_precision_at_k": round(precision_at_k, 6),
            "retrieval_recall_at_k": round(recall_at_k, 6),
            "retrieval_mrr": round(mrr, 6),
            "retrieval_hit_rate": round(hit_rate, 6),
            "retrieval_n_queries": max(0, n_queries),
            "retrieval_mean_latency_ms": round(max(0.0, mean_latency_ms), 2),
        }
        self._safe_log_metrics(metrics, step=step)
        self._safe_log_param("retrieval_k", str(k))

    def log_retrieval_params(
        self,
        embedding_model: str,
        vector_store: str,
        search_strategy: str,
        use_hyde: bool,
        use_reranking: bool,
        reranker_model: str,
        top_k_retrieve: int,
        top_k_rerank: int,
    ):
        """Log retrieval configuration parameters."""
        self._safe_log_params(
            {
                "retrieval_embedding_model": embedding_model,
                "retrieval_vector_store": vector_store,
                "retrieval_search_strategy": search_strategy,
                "retrieval_use_hyde": str(use_hyde),
                "retrieval_use_reranking": str(use_reranking),
                "retrieval_reranker_model": reranker_model,
                "retrieval_top_k_retrieve": str(max(1, top_k_retrieve)),
                "retrieval_top_k_rerank": str(max(1, top_k_rerank)),
            }
        )

    def log_rag_metrics(
        self,
        faithfulness: float,
        answer_relevance: float,
        context_precision: float,
        bleu_1: float,
        bleu_4: float,
        rouge_1_f: float,
        rouge_l_f: float,
        n_queries: int,
        mean_latency_ms: float,
        step: Optional[int] = None,
    ):
        """Log RAG evaluation metrics including composite score."""
        # ✅ Clamp metrics to valid ranges
        faithfulness = max(0.0, min(1.0, faithfulness))
        answer_relevance = max(0.0, min(1.0, answer_relevance))
        context_precision = max(0.0, min(1.0, context_precision))

        metric_values = {
            "faithfulness": faithfulness,
            "answer_relevance": answer_relevance,
            "context_precision": context_precision,
        }
        metrics = {
            "rag_faithfulness": round(faithfulness, 6),
            "rag_answer_relevance": round(answer_relevance, 6),
            "rag_context_precision": round(context_precision, 6),
            "rag_bleu_1": round(max(0.0, bleu_1), 6),
            "rag_bleu_4": round(max(0.0, bleu_4), 6),
            "rag_rouge_1_f": round(max(0.0, rouge_1_f), 6),
            "rag_rouge_l_f": round(max(0.0, rouge_l_f), 6),
            "rag_n_queries": max(0, n_queries),
            "rag_mean_latency_ms": round(max(0.0, mean_latency_ms), 2),
            "rag_composite_score": self._compute_composite(metric_values),
        }
        self._safe_log_metrics(metrics, step=step)

    def log_rag_params(
        self,
        llm_model: str,
        temperature: float,
        max_tokens: int,
        prompt_version: str = "v1",
    ):
        """Log RAG generation parameters."""
        self._safe_log_params(
            {
                "rag_llm_model": llm_model,
                "rag_temperature": str(max(0.0, min(2.0, temperature))),
                "rag_max_tokens": str(max(1, max_tokens)),
                "rag_prompt_version": prompt_version,
            }
        )

    def log_ingestion_event(
        self,
        source_file: str,
        document_type: str,
        page_count: int,
        child_chunks: int,
        parent_chunks: int,
        ocr_confidence: float,
        vision_fallbacks: int,
        total_latency_s: float,
        file_size_mb: float,
        correlation_id: Optional[str] = None,
    ):
        """
        Log a document ingestion event as a nested MLflow run.

        Creates a child run under the current active run (if any) for hierarchical tracking.
        """
        corr_id = correlation_id or "ingest_event"
        # Auto-detect nesting based on active run
        active_run = mlflow.active_run()
        use_nested = active_run is not None

        with self.start_run(
            run_name=f"ingest_{Path(source_file).stem}",
            tags={"event_type": "ingestion", "document_type": document_type},
            nested=use_nested,
            correlation_id=corr_id,
        ):
            self._safe_log_params(
                {
                    "ingest_source_file": os.path.basename(source_file),
                    "ingest_document_type": document_type,
                }
            )
            # ✅ Clamp numeric inputs
            ocr_confidence = max(0.0, min(1.0, ocr_confidence))
            page_count = max(0, page_count)
            child_chunks = max(0, child_chunks)
            parent_chunks = max(0, parent_chunks)
            vision_fallbacks = max(0, vision_fallbacks)
            total_latency_s = max(0.0, total_latency_s)
            file_size_mb = max(0.0, file_size_mb)

            self._safe_log_metrics(
                {
                    "ingest_page_count": page_count,
                    "ingest_child_chunks": child_chunks,
                    "ingest_parent_chunks": parent_chunks,
                    "ingest_ocr_confidence": round(ocr_confidence, 4),
                    "ingest_vision_fallbacks": vision_fallbacks,
                    "ingest_latency_seconds": round(total_latency_s, 3),
                    "ingest_file_size_mb": round(file_size_mb, 3),
                    "ingest_chunks_per_page": round(child_chunks / max(page_count, 1), 2),
                    "ingest_seconds_per_page": round(total_latency_s / max(page_count, 1), 3),
                }
            )

    @contextmanager
    def timer(self, metric_name: str, step: Optional[int] = None):
        """
        Context manager for timing code blocks and logging to MLflow.

        Usage:
            with logger.timer("retrieval_latency_ms"):
                results = retrieve(query)
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._safe_log_metrics({metric_name: round(elapsed_ms, 2)}, step=step)

    def log_eval_results_csv(
        self,
        results: List[Dict[str, Any]],
        filename: str = "eval_results.csv",
        artifact_path: str = "evaluation",
        correlation_id: Optional[str] = None,
    ):
        """
        Log evaluation results as a CSV artifact to MLflow.

        Args:
            results: List of dicts (each dict = one row)
            filename: Name for the CSV file
            artifact_path: MLflow artifact subpath
            correlation_id: Request ID for distributed tracing
        """
        if not results:
            logger.debug("No evaluation results to log")
            return

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=results[0].keys())
                writer.writeheader()
                writer.writerows(results)
                tmp_path = f.name

            mlflow.log_artifact(tmp_path, artifact_path=artifact_path)
            os.unlink(tmp_path)
            logger.debug(f"Logged eval CSV: {filename}")
        except Exception as e:
            logger.warning(f"Could not log eval CSV '{filename}': {e}")

    async def log_eval_results_csv_async(
        self,
        results: List[Dict[str, Any]],
        filename: str = "eval_results.csv",
        artifact_path: str = "evaluation",
        correlation_id: Optional[str] = None,
    ):
        """Async version of log_eval_results_csv for non-blocking artifact logging."""
        await asyncio.to_thread(self.log_eval_results_csv, results, filename, artifact_path, correlation_id)

    def _safe_log_params(self, params: Dict[str, str]):
        """Log parameters with truncation to avoid MLflow length limits."""

        def _smart_truncate(value: str, max_len: int = 500) -> str:
            if len(value) <= max_len:
                return value
            return value[: max_len - 3] + "..."

        truncated = {k: _smart_truncate(scrub_pii_for_evaluation(str(v), domain="general")) for k, v in params.items()}
        try:
            mlflow.log_params(truncated)
        except Exception as e:
            logger.debug("MLflow log_params failed: %s", e)

    def _safe_log_param(self, key: str, value: str):
        """Log a single parameter with truncation."""
        try:
            def _smart_truncate(value: str, max_len: int = 500) -> str:
                if len(value) <= max_len:
                    return value
                return value[: max_len - 3] + "..."

            mlflow.log_param(
                key,
                _smart_truncate(scrub_pii_for_evaluation(str(value), domain="general")),
            )
        except Exception as e:
            logger.debug("MLflow log_param failed: %s", e)

    @staticmethod
    def _compute_composite(values: Dict[str, float]) -> float:
        """
        Compute composite RAG score as mean of key metrics.

        Args:
            values: Dict with metric names and values

        Returns:
            Mean of available composite metrics, or 0.0 if none available
        """
        selected = [values[k] for k in COMPOSITE_METRICS if k in values and values[k] is not None]
        return round(sum(selected) / len(selected), 6) if selected else 0.0

    @property
    def is_available(self) -> bool:
        """Check if MLflow logging is currently active."""
        return self.__class__._mlflow_available

    def reset_circuit_breaker(self):
        """Manually reset circuit breaker (e.g., after fixing MLflow server)."""
        self.__class__._mlflow_available = True
        self.__class__._failure_count = 0
        logger.info("MLflow circuit breaker reset — logging re-enabled")


def get_mlflow_metadata() -> dict[str, Any]:
    """✅ NEW: Return MLflow logger metadata for debugging."""
    return {
        "composite_metrics": COMPOSITE_METRICS,
        "failure_threshold": MLflowLogger._FAILURE_THRESHOLD,
        "socket_timeout_seconds": _SOCKET_TIMEOUT,
        "param_max_length": 500,
        "circuit_breaker_enabled": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "configure_mlflow",
    "MLflowLogger",
    "NULL_RUN",
    "get_mlflow_metadata",
]
# Local smoke test entry point. Run: python -m

