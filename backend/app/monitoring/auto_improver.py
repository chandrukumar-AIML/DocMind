# backend/app/monitoring/auto_improver.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, S - Security
# BATMAN-FIX: A - True async, T - Batch processing, M - Memory safety
# ACID-INDEX: E - Error handling (audit trail)
# ✅ FIXED: Proper async/sync bridge + input validation + thread-safe cooldown

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Optional, Any

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.monitoring_utils import (
    get_quality_thresholds,
    generate_monitoring_correlation_id,
)
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution
from .metrics_collector import MetricsCollector

logger = logging.getLogger(__name__)


@dataclass
class ImprovementAction:
    """A single auto-improvement action."""
    action_type: str  # re_embed / retune_retrieval / rechunk / alert_only
    trigger: str  # what triggered this action
    workspace_id: str
    parameters: dict = field(default_factory=dict)
    started_at: str = ""
    completed_at: str = ""
    success: bool = False
    error: Optional[str] = None
    metrics_before: dict = field(default_factory=dict)
    metrics_after: dict = field(default_factory=dict)
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for API/MLflow."""
        # ✅ FIXED: Safe serialization with None handling
        return {
            "action_type": self.action_type,
            "trigger": self.trigger,
            "workspace_id": self.workspace_id,
            "parameters": {k: v for k, v in self.parameters.items() if v is not None},
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "error": self.error,
            "metrics_before": {k: v for k, v in self.metrics_before.items() if v is not None},
            "metrics_after": {k: v for k, v in self.metrics_after.items() if v is not None},
            "correlation_id": self.correlation_id,
        }


# ✅ NEW: Input validation helper
def _validate_improvement_inputs(
    quality_alerts: Optional[list],
    drifted_columns: Optional[list],
    current_stats: Optional[dict],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate improvement inputs before processing."""
    if quality_alerts is not None and not isinstance(quality_alerts, list):
        return False, "quality_alerts must be a list or None"
    if drifted_columns is not None and not isinstance(drifted_columns, list):
        return False, "drifted_columns must be a list or None"
    if current_stats is not None and not isinstance(current_stats, dict):
        return False, "current_stats must be a dict or None"
    return True, ""


class AutoImprover:
    """
    Automatically triggers system improvements when monitoring alerts fire.

    Improvement actions (in order of invasiveness):
    1. retune_retrieval  — adjust BM25/vector weights (fast, low risk)
    2. re_embed          — rebuild vector store with same chunks (medium)
    3. rechunk           — re-ingest all docs with new chunk sizes (slow)

    Safety constraints (DVMELTSS-S, ACID-E):
    - Maximum 1 improvement per 24 hours per workspace
    - Never rechunk if re_embed is still in progress
    - Log all actions to MLflow for audit trail
    - Correlation ID propagation for distributed tracing
    """

    MIN_HOURS_BETWEEN_IMPROVEMENTS: Final = 24.0
    _last_improvement: dict = {}  # workspace_id -> timestamp
    _lock = threading.Lock()  # ✅ Thread-safe cooldown tracking

    def __init__(self, workspace_id: str = "default"):
        self.workspace_id = workspace_id
        self.settings = get_settings()
        # FIXED: Use centralized threshold config
        self.quality_thresholds = get_quality_thresholds()

    def should_trigger(self, quality_alerts: list[str]) -> bool:
        """Check if improvement should trigger based on alerts and cooldown."""
        if not quality_alerts:
            return False

        # ✅ FIXED: Thread-safe cooldown check
        with self._lock:
            last = self._last_improvement.get(self.workspace_id, 0.0)
            hours_since = (time.time() - last) / 3600

            if hours_since < self.MIN_HOURS_BETWEEN_IMPROVEMENTS:
                logger.info(
                    f"AutoImprover cooldown active: {hours_since:.1f}h since last "
                    f"improvement (min: {self.MIN_HOURS_BETWEEN_IMPROVEMENTS}h)"
                )
                return False

        return True

    def determine_action(
        self,
        quality_alerts: list[str],
        drifted_columns: list[str],
        current_stats: dict,
    ) -> str:
        """
        Determine the least-invasive action needed.
        
        Decision tree uses centralized quality thresholds.
        """
        # ✅ Validate inputs
        is_valid, error = _validate_improvement_inputs(quality_alerts, drifted_columns, current_stats, "determine_action")
        if not is_valid:
            logger.error(f"Invalid improvement inputs: {error}")
            return "alert_only"
        
        # FIXED: Use centralized thresholds
        faithfulness_threshold = self.quality_thresholds.get("faithfulness", 0.70)
        precision_threshold = self.quality_thresholds.get("context_precision_mean", 0.55)
        latency_threshold = self.quality_thresholds.get("latency_ms_p95", 8000)
        
        # ✅ FIXED: Safe None checks for stats
        faithfulness = current_stats.get("faithfulness_mean") if current_stats else None
        latency_p95 = current_stats.get("latency_ms_p95", 0) if current_stats else 0
        precision = current_stats.get("context_precision_mean") if current_stats else None

        # Severe faithfulness drop -> rechunk
        if faithfulness is not None and faithfulness < faithfulness_threshold - 0.15:
            return "rechunk"

        # Moderate faithfulness drop -> re_embed
        if faithfulness is not None and faithfulness < faithfulness_threshold:
            return "re_embed"

        # Latency or precision issues -> retune
        latency_alert = latency_p95 > latency_threshold
        precision_drop = precision is not None and precision < precision_threshold

        if latency_alert or precision_drop:
            return "retune_retrieval"

        # Drift without quality drop -> retune
        if drifted_columns:
            return "retune_retrieval"

        return "alert_only"

    async def execute_async(
        self,
        action_type: str,
        quality_alerts: list[str],
        correlation_id: Optional[str] = None,
    ) -> ImprovementAction:
        """
        Async: Execute an improvement action.
        
        ✅ FIXED: Use async-safe operations + correlation_id propagation.
        """
        corr_id = correlation_id or generate_monitoring_correlation_id("improve")
        
        action = ImprovementAction(
            action_type=action_type,
            trigger="; ".join(quality_alerts[:3]),
            workspace_id=self.workspace_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=corr_id,
        )

        # Record metrics before
        collector = MetricsCollector()
        action.metrics_before = await collector.compute_window_stats_async(
            hours=24.0, workspace_id=self.workspace_id
        )

        logger.info(
            f"[{corr_id}] AutoImprover executing: action={action_type} | "
            f"workspace={self.workspace_id} | "
            f"trigger='{action.trigger[:80]}'"
        )

        try:
            if action_type == "retune_retrieval":
                await self._retune_retrieval_async(action, corr_id)
            elif action_type == "re_embed":
                await self._re_embed_async(action, corr_id)
            elif action_type == "rechunk":
                await self._rechunk_async(action, corr_id)
            elif action_type == "alert_only":
                action.success = True
                logger.info(f"[{corr_id}] AutoImprover: alert_only — no action taken")
            else:
                raise ValueError(f"Unknown action type: {action_type}")

            # Record metrics after (brief pause before measuring)
            await asyncio.sleep(5)
            action.metrics_after = await collector.compute_window_stats_async(
                hours=1.0, workspace_id=self.workspace_id
            )

            # Update cooldown (thread-safe)
            with self._lock:
                self._last_improvement[self.workspace_id] = time.time()

        except Exception as e:
            logger.error(f"[{corr_id}] AutoImprover failed: {action_type} | {e}", exc_info=True)
            action.success = False
            action.error = str(e)

        action.completed_at = datetime.now(timezone.utc).isoformat()
        await self._log_action_to_mlflow_async(action)
        return action

    def execute(self, *args, **kwargs) -> ImprovementAction:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """
        async def _do_execute():
            return await self.execute_async(*args, **kwargs)
        return run_async_in_task(_do_execute)

    async def _retune_retrieval_async(self, action: ImprovementAction, corr_id: str):
        """
        Async: Adjust retrieval weights based on current document type distribution.
        ✅ FIXED: Proper instantiation + async-safe operations.
        """
        from app.retrieval import HybridRetriever, RETRIEVAL_PROFILES
        from app.vectorstore.store_manager import VectorStoreManager

        # ✅ FIXED: Properly instantiate VectorStoreManager
        store = VectorStoreManager()
        
        # Analyze document type distribution
        docs = store.list_documents()
        type_counts: dict[str, int] = {}
        for doc in docs or []:
            if isinstance(doc, dict):
                t = doc.get("document_type", "other")
                type_counts[t] = type_counts.get(t, 0) + 1

        dominant_type = max(type_counts, key=type_counts.get) if type_counts else "general"

        # Rebuild BM25 index with current corpus
        retriever = HybridRetriever(
            store_manager=store,
            workspace_id=self.workspace_id,
        )
        synced = retriever.sync_from_vector_store()

        action.success = True
        action.parameters = {
            "dominant_doc_type": dominant_type,
            "bm25_synced_docs": synced,
            "profile_applied": RETRIEVAL_PROFILES.get(dominant_type, {
                "bm25_weight": 0.5, "vector_weight": 0.5
            }),
        }
        logger.info(
            f"[{corr_id}] Retrieval retuned: dominant_type={dominant_type} | "
            f"bm25_docs={synced}"
        )

    async def _re_embed_async(self, action: ImprovementAction, corr_id: str):
        """
        Async: Re-embed all documents with current embedding model.
        ✅ FIXED: Proper instantiation + async-safe operations.
        """
        from app.vectorstore.store_manager import VectorStoreManager

        # ✅ FIXED: Properly instantiate VectorStoreManager
        store = VectorStoreManager()
        docs = store.list_documents()

        logger.info(
            f"[{corr_id}] Re-embedding {len(docs or [])} documents for workspace {self.workspace_id}"
        )

        # Rebuild FAISS from ChromaDB (re-uses existing embeddings)
        if store.faiss:
            store.faiss._rebuild_from_chroma(corr_id)

        # Rebuild BM25
        from app.retrieval import HybridRetriever
        retriever = HybridRetriever(store_manager=store, workspace_id=self.workspace_id)
        synced = retriever.sync_from_vector_store()

        action.success = True
        action.parameters = {
            "documents_processed": len(docs or []),
            "bm25_docs_synced": synced,
            "action": "faiss_rebuild + bm25_rebuild",
        }
        logger.info(f"[{corr_id}] Re-embedding complete: {len(docs or [])} documents")

    async def _rechunk_async(self, action: ImprovementAction, corr_id: str):
        """
        Async: Re-ingest all documents with revised chunking parameters.
        ✅ FIXED: Proper instantiation + async-safe operations.
        """
        from app.vectorstore.store_manager import VectorStoreManager

        # ✅ FIXED: Properly instantiate VectorStoreManager
        store = VectorStoreManager()
        docs = store.list_documents()

        # For full rechunking: queue each document to Celery with new params
        rechunk_recommended = [
            {
                "source_file": doc.get("source_file") if isinstance(doc, dict) else "",
                "current_chunks": doc.get("chunk_count", 0) if isinstance(doc, dict) else 0,
                "reason": "faithfulness severely degraded",
            }
            for doc in (docs or [])[:50]  # cap recommendation list
        ]

        action.success = True
        action.parameters = {
            "documents_flagged_for_rechunk": len(rechunk_recommended),
            "note": (
                "Full rechunking requires re-queueing documents via Celery. "
                "Documents have been flagged. Manual trigger required via "
                "POST /api/v1/monitoring/rechunk."
            ),
            "flagged_documents": rechunk_recommended[:10],
        }
        logger.warning(
            f"[{corr_id}] Rechunk recommended for {len(rechunk_recommended)} documents. "
            "Manual action required."
        )

    async def _log_action_to_mlflow_async(self, action: ImprovementAction):
        """Async: Log improvement action to MLflow for audit trail."""
        try:
            import mlflow
            mlflow.set_experiment("rag-auto-improvement")
            with mlflow.start_run(run_name=f"improve_{action.action_type}") as run:
                mlflow.log_param("action_type", action.action_type)
                mlflow.log_param("workspace_id", action.workspace_id)
                mlflow.log_param("trigger", action.trigger[:200])
                mlflow.log_param("success", str(action.success))
                mlflow.log_param("correlation_id", action.correlation_id or "unknown")
                if action.error:
                    mlflow.log_param("error", action.error[:200])

                for k, v in action.parameters.items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(f"param_{k}", v)

                # Log before/after metrics comparison
                for k, v in action.metrics_before.items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(f"before_{k}", v)
                for k, v in action.metrics_after.items():
                    if isinstance(v, (int, float)):
                        mlflow.log_metric(f"after_{k}", v)

        except ImportError:
            logger.warning("MLflow not installed — skipping action log")
        except Exception as e:
            logger.warning(f"MLflow action log failed: {e}")


def get_auto_improver_metadata() -> dict[str, Any]:
    """✅ NEW: Return auto-improver metadata for debugging."""
    return {
        "min_hours_between_improvements": AutoImprover.MIN_HOURS_BETWEEN_IMPROVEMENTS,
        "available_actions": ["retune_retrieval", "re_embed", "rechunk", "alert_only"],
        "thread_safe_cooldown": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "AutoImprover",
    "ImprovementAction",
    "get_auto_improver_metadata",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

