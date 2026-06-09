# backend/app/monitoring/evidently_monitor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Proper async/sync bridge + input validation + safe DataFrame handling

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Final, Optional, Any

import numpy as np
import pandas as pd

# DVMELTSS-M: Import centralized utilities
from app.core.monitoring_utils import (
    get_quality_thresholds,
    validate_monitoring_window,
    generate_monitoring_correlation_id,
)
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution
from .metrics_collector import MetricsCollector, QueryMetrics

logger = logging.getLogger(__name__)


@dataclass
class DriftReport:
    """Results from Evidently drift analysis."""

    run_date: str
    workspace_id: str
    n_current: int
    n_reference: int
    drift_detected: bool
    drifted_columns: list[str] = field(default_factory=list)
    column_drift: dict = field(default_factory=dict)
    quality_alerts: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    mlflow_run_id: str = ""
    report_path: str = ""
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for API/MLflow."""
        # ✅ FIXED: Safe serialization with None handling
        return {
            "run_date": self.run_date,
            "workspace_id": self.workspace_id,
            "n_current": self.n_current,
            "n_reference": self.n_reference,
            "drift_detected": self.drift_detected,
            "drifted_columns": self.drifted_columns or [],
            "column_drift": {k: v for k, v in (self.column_drift or {}).items() if v is not None},
            "quality_alerts": self.quality_alerts or [],
            "recommendations": self.recommendations or [],
            "mlflow_run_id": self.mlflow_run_id,
            "correlation_id": self.correlation_id,
        }


# ✅ NEW: Input validation helper
def _validate_drift_inputs(
    current_hours: float,
    reference_hours: float,
    workspace_id: str,
    corr_id: str,
) -> tuple[bool, str]:
    """Validate drift analysis inputs before processing."""
    if not isinstance(current_hours, (int, float)) or current_hours <= 0:
        return False, "current_hours must be a positive number"
    if not isinstance(reference_hours, (int, float)) or reference_hours <= 0:
        return False, "reference_hours must be a positive number"
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return False, "workspace_id must be a non-empty string"
    if reference_hours <= current_hours:
        return False, "reference_hours must be greater than current_hours"
    return True, ""


class EvidentlyMonitor:
    """
    Uses Evidently AI to detect distribution drift in RAG metrics.

    Features (DVMELTSS-V, BATMAN-A):
    - Configurable quality thresholds via settings
    - Async-safe statistical computations
    - Correlation ID propagation for tracing
    - Graceful fallback to manual KS tests if Evidently unavailable
    """

    # Monitored columns for drift detection
    MONITORED_COLUMNS: Final = [
        "latency_ms",
        "confidence_score",
        "relevance_score",
        "answer_length",
        "retrieval_count",
        "retry_count",
        "web_search_used",
        "is_grounded",
    ]

    def __init__(self, workspace_id: str = "default", thresholds: Optional[dict] = None):
        self.workspace_id = workspace_id
        # FIXED: Use centralized threshold getter with overrides
        self.quality_thresholds = get_quality_thresholds(thresholds)
        self.collector = MetricsCollector()

    def run(
        self,
        current_hours: float = 168.0,  # 7 days
        reference_hours: float = 336.0,  # 14 days (prior 7 days)
        log_to_mlflow: bool = True,
        correlation_id: Optional[str] = None,
    ) -> DriftReport:
        """
        Run drift detection comparing current vs reference window.

        ✅ FIXED: Proper async handling + input validation.
        """
        corr_id = correlation_id or generate_monitoring_correlation_id("drift")

        # ✅ Validate inputs
        is_valid, error = _validate_drift_inputs(current_hours, reference_hours, self.workspace_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid drift inputs: {error}")
            return DriftReport(
                run_date=date.today().isoformat(),
                workspace_id=self.workspace_id,
                n_current=0,
                n_reference=0,
                drift_detected=False,
                quality_alerts=[f"Invalid inputs: {error}"],
                correlation_id=corr_id,
            )

        # FIXED: Validate windows using centralized utility
        current_hours = validate_monitoring_window(current_hours, min_hours=24, max_hours=720)
        reference_hours = validate_monitoring_window(reference_hours, min_hours=48, max_hours=1440)

        report = DriftReport(
            run_date=date.today().isoformat(),
            workspace_id=self.workspace_id,
            n_current=0,
            n_reference=0,
            drift_detected=False,
            correlation_id=corr_id,
        )

        # ✅ FIXED: Use run_async_in_task for safe async execution
        async def _get_current():
            return await self.collector.get_recent_async(hours=current_hours, workspace_id=self.workspace_id)

        async def _get_reference():
            return await self.collector.get_recent_async(hours=reference_hours, workspace_id=self.workspace_id)

        try:
            current_metrics = run_async_in_task(_get_current, timeout=30.0)
            reference_metrics = run_async_in_task(_get_reference, timeout=30.0)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to fetch metrics: {e}")
            report.quality_alerts.append(f"Metrics fetch failed: {e}")
            return report

        # Reference = prior window (exclude current)
        cutoff_ts = time.time() - (current_hours * 3600)
        reference_metrics = [m for m in reference_metrics if m.timestamp < cutoff_ts]

        report.n_current = len(current_metrics)
        report.n_reference = len(reference_metrics)

        if len(current_metrics) < 10:
            logger.info(
                f"[{corr_id}] Insufficient current data for drift analysis: "
                f"{len(current_metrics)} samples (need ≥ 10)"
            )
            report.quality_alerts.append(
                "Insufficient query volume for statistical drift detection " f"({len(current_metrics)} samples)"
            )
            return report

        if len(reference_metrics) < 10:
            logger.info(f"[{corr_id}] No reference window data — skipping drift comparison")
            # Still check quality thresholds on current window
            current_stats = self.collector.compute_window_stats(
                hours=current_hours,
                workspace_id=self.workspace_id,
            )
            report.quality_alerts = self._check_quality_thresholds(current_stats)
            report.recommendations = self._build_recommendations(report.quality_alerts, {})
            return report

        # Convert to DataFrames
        current_df = self._metrics_to_df(current_metrics)
        reference_df = self._metrics_to_df(reference_metrics)

        # ✅ Handle empty DataFrames
        if current_df.empty or reference_df.empty:
            logger.warning(f"[{corr_id}] Empty DataFrame after conversion — skipping drift analysis")
            report.quality_alerts.append("Insufficient valid data for drift analysis")
            return report

        # Run Evidently drift detection
        column_drift = self._run_evidently(current_df, reference_df, corr_id)
        report.column_drift = column_drift
        report.drifted_columns = [col for col, result in column_drift.items() if result.get("drift_detected", False)]
        report.drift_detected = len(report.drifted_columns) > 0

        # Check quality thresholds (using centralized config)
        current_stats = self.collector.compute_window_stats(
            hours=current_hours,
            workspace_id=self.workspace_id,
        )
        report.quality_alerts = self._check_quality_thresholds(current_stats)
        report.recommendations = self._build_recommendations(report.quality_alerts, column_drift)

        # Log to MLflow with correlation_id
        if log_to_mlflow:
            report.mlflow_run_id = self._log_to_mlflow(report, current_df, reference_df, current_stats, corr_id)

        logger.info(
            f"[{corr_id}] Evidently monitor: workspace={self.workspace_id} | "
            f"drift={report.drift_detected} | "
            f"drifted={report.drifted_columns} | "
            f"alerts={len(report.quality_alerts)}"
        )
        return report

    def _metrics_to_df(self, metrics: list[QueryMetrics]) -> pd.DataFrame:
        """Convert metrics list to DataFrame for Evidently."""
        # ✅ FIXED: Handle empty/None metrics
        if not metrics:
            return pd.DataFrame()

        rows = []
        for m in metrics:
            if m is None:
                continue
            rows.append(
                {
                    "latency_ms": m.latency_ms,
                    "confidence_score": m.confidence_score,
                    "relevance_score": m.relevance_score,
                    "answer_length": float(m.answer_length),
                    "retrieval_count": float(m.retrieval_count),
                    "retry_count": float(m.retry_count),
                    "web_search_used": float(m.web_search_used),
                    "is_grounded": float(m.is_grounded),
                    "crag_generate": float(m.crag_action == "generate"),
                    "crag_rewrite": float(m.crag_action == "rewrite"),
                    "crag_web_search": float(m.crag_action == "web_search"),
                    "faithfulness": m.faithfulness if m.faithfulness is not None else np.nan,
                }
            )

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        return df.dropna(subset=["latency_ms", "confidence_score"])

    def _run_evidently(
        self,
        current_df: pd.DataFrame,
        reference_df: pd.DataFrame,
        corr_id: str,
    ) -> dict:
        """Run Evidently drift detection for each monitored column."""
        column_results = {}

        try:
            from evidently.report import Report
            from evidently.metrics import ColumnDriftMetric

            # Build column-level report
            report = Report(
                metrics=[
                    ColumnDriftMetric(column_name=col)
                    for col in self.MONITORED_COLUMNS
                    if col in current_df.columns and col in reference_df.columns
                ]
            )
            report.run(
                current_data=current_df,
                reference_data=reference_df,
            )

            # Extract results from report
            report_dict = report.as_dict()
            for metric_result in report_dict.get("metrics", []):
                col = metric_result.get("result", {}).get("column_name", "")
                if not col:
                    continue

                result = metric_result.get("result", {})
                column_results[col] = {
                    "drift_detected": bool(result.get("drift_detected", False)),
                    "stattest_name": result.get("stattest_name", ""),
                    "drift_score": round(float(result.get("drift_score", 0.0)), 4),
                    "p_value": round(float(result.get("p_value", 1.0)), 4),
                }

        except ImportError:
            logger.warning(f"[{corr_id}] Evidently not installed — using manual drift detection")
            column_results = self._manual_drift_detection(current_df, reference_df, corr_id)
        except Exception as e:
            logger.error(f"[{corr_id}] Evidently drift analysis failed: {e}")
            column_results = self._manual_drift_detection(current_df, reference_df, corr_id)

        return column_results

    @staticmethod
    def _manual_drift_detection(
        current_df: pd.DataFrame,
        reference_df: pd.DataFrame,
        corr_id: str,
        p_threshold: float = 0.05,
    ) -> dict:
        """KS test fallback when Evidently is unavailable."""
        from scipy import stats

        results = {}
        for col in [
            "latency_ms",
            "confidence_score",
            "relevance_score",
            "answer_length",
            "retrieval_count",
        ]:
            if col not in current_df.columns:
                continue
            curr = current_df[col].dropna().values
            ref = reference_df[col].dropna().values if col in reference_df.columns else np.array([])

            # ✅ FIXED: Safe length checks for KS test
            if len(curr) < 5 or len(ref) < 5:
                results[col] = {
                    "drift_detected": False,
                    "stattest_name": "ks_insufficient_data",
                }
                continue

            try:
                stat, p_val = stats.ks_2samp(curr, ref)
                results[col] = {
                    "drift_detected": p_val < p_threshold,
                    "stattest_name": "ks_2samp",
                    "drift_score": round(float(stat), 4),
                    "p_value": round(float(p_val), 4),
                }
            except Exception as e:
                logger.warning(f"[{corr_id}] KS test failed for {col}: {e}")
                results[col] = {"drift_detected": False, "stattest_name": "ks_error"}
        return results

    def _check_quality_thresholds(self, stats: dict) -> list[str]:
        """Check current window stats against quality alert thresholds."""
        alerts = []
        # FIXED: Use centralized threshold config
        thresholds = self.quality_thresholds

        for metric, threshold in thresholds.items():
            val = stats.get(metric)
            if val is None:
                continue

            if metric in ("latency_ms_p95", "web_search_rate", "human_review_rate"):
                # Higher = worse
                if val > threshold:
                    alerts.append(f"{metric}: {val:.3f} exceeds threshold {threshold}")
            else:
                # Lower = worse
                if val < threshold:
                    alerts.append(f"{metric}: {val:.3f} below threshold {threshold}")

        return alerts

    @staticmethod
    def _build_recommendations(
        quality_alerts: list[str],
        column_drift: dict,
    ) -> list[str]:
        """Map alerts and drift to actionable recommendations."""
        recommendations = []

        # Quality-based recommendations
        for alert in quality_alerts:
            if "faithfulness" in alert:
                recommendations.append(
                    "Faithfulness degraded: consider re-embedding with smaller chunks "
                    "and higher overlap to improve context precision."
                )
            elif "latency" in alert:
                recommendations.append(
                    "P95 latency high: profile retrieval step, consider reducing "
                    "top_k_retrieve or enabling FAISS-only mode."
                )
            elif "web_search_rate" in alert:
                recommendations.append(
                    "High web search rate: document corpus may be missing key topics. "
                    "Ingest additional relevant documents."
                )
            elif "confidence" in alert:
                recommendations.append(
                    "Low confidence scores: check if recent document additions "
                    "are semantically aligned with query patterns."
                )

        # Drift-based recommendations
        drifted = [col for col, r in column_drift.items() if r.get("drift_detected")]
        if "latency_ms" in drifted:
            recommendations.append(
                "Latency drift detected: system is getting slower. " "Check ChromaDB collection size and FAISS index."
            )
        if "confidence_score" in drifted:
            recommendations.append(
                "Confidence drift detected: run RAGAs evaluation to identify " "failing query categories."
            )

        # Deduplicate
        return list(dict.fromkeys(recommendations))

    def _log_to_mlflow(
        self,
        report: DriftReport,
        current_df: pd.DataFrame,
        reference_df: pd.DataFrame,
        stats: dict,
        correlation_id: str,
    ) -> str:
        """Log drift report to MLflow with correlation_id."""
        try:
            import mlflow

            mlflow.set_experiment("rag-monitoring")
            with mlflow.start_run(run_name=f"monitoring_{report.run_date}") as run:
                # Log params
                mlflow.log_param("workspace_id", report.workspace_id)
                mlflow.log_param("n_current", report.n_current)
                mlflow.log_param("n_reference", report.n_reference)
                mlflow.log_param("drift_detected", str(report.drift_detected))
                mlflow.log_param("drifted_columns", ",".join(report.drifted_columns))
                mlflow.log_param("correlation_id", correlation_id)

                # Log metrics
                for key, val in stats.items():
                    if isinstance(val, (int, float)):
                        mlflow.log_metric(f"monitor_{key}", val)

                # Log drift scores per column
                for col, result in report.column_drift.items():
                    drift_score = result.get("drift_score", 0)
                    if isinstance(drift_score, (int, float)):
                        mlflow.log_metric(f"drift_{col}", float(drift_score))

                # Save report JSON as artifact
                with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                    json.dump(report.to_dict() | {"window_stats": stats}, f, indent=2)
                    tmp = f.name

                mlflow.log_artifact(tmp, artifact_path="monitoring_reports")
                os.unlink(tmp)

                return run.info.run_id

        except ImportError:
            logger.warning(f"[{correlation_id}] MLflow not installed — skipping monitoring log")
            return ""
        except Exception as e:
            logger.warning(f"[{correlation_id}] MLflow monitoring log failed: {e}")
            return ""


def get_evidently_metadata() -> dict[str, Any]:
    """✅ NEW: Return evidently monitor metadata for debugging."""
    return {
        "monitored_columns": EvidentlyMonitor.MONITORED_COLUMNS,
        "default_current_hours": 168.0,  # 7 days
        "default_reference_hours": 336.0,  # 14 days
        "min_samples_for_analysis": 10,
        "ks_test_p_threshold": 0.05,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "EvidentlyMonitor",
    "DriftReport",
    "get_evidently_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
