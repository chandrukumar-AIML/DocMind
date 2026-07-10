
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Any

# DVMELTSS-M: Import monitoring components
from .metrics_collector import MetricsCollector
from .evidently_monitor import EvidentlyMonitor, DriftReport
from .auto_improver import AutoImprover, ImprovementAction
from app.evaluation.alert_engine import AlertEngine
from app.core.monitoring_utils import generate_monitoring_correlation_id
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution

logger = logging.getLogger(__name__)

_STEP_TIMEOUT: int = 120


@dataclass
class MonitoringRunResult:
    """Complete result of a monitoring pipeline run."""

    run_id: str
    workspace_id: str
    started_at: str
    completed_at: str = ""

    # Window statistics
    window_stats: dict = field(default_factory=dict)

    # Drift analysis
    drift_report: Optional[DriftReport] = None

    # Auto-improvement
    improvement: Optional[ImprovementAction] = None

    # Alerts sent
    alerts_sent: list[str] = field(default_factory=list)

    # Error
    error: Optional[str] = None

    correlation_id: Optional[str] = None

    @property
    def is_healthy(self) -> bool:
        stats = self.window_stats
        faith = stats.get("faithfulness_mean")
        return (not self.drift_report or not self.drift_report.drift_detected) and (faith is None or faith >= 0.70)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "run_id": self.run_id,
            "workspace_id": self.workspace_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "window_stats": {k: v for k, v in self.window_stats.items() if v is not None},
            "drift_report": self.drift_report.to_dict() if self.drift_report else None,
            "improvement": self.improvement.to_dict() if self.improvement else None,
            "alerts_sent": self.alerts_sent or [],
            "error": self.error,
            "is_healthy": self.is_healthy,
            "correlation_id": self.correlation_id,
        }


def _validate_pipeline_inputs(
    log_to_mlflow: Optional[bool],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate pipeline inputs before processing."""
    if log_to_mlflow is not None and not isinstance(log_to_mlflow, bool):
        return False, "log_to_mlflow must be a boolean or None"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class MonitoringPipeline:
    """
    Orchestrates the full monitoring pipeline:
    1. Compute rolling window statistics
    2. Run Evidently drift detection
    3. Check quality thresholds
    4. Trigger auto-improvement if needed
    5. Send alerts
    6. Persist daily stats

    Features (DVMELTSS-A, BATMAN-T, ASCALE-L):
    - Fully async orchestration with proper error handling
    - Correlation ID propagation across all components
    - Configurable quality thresholds via centralized config
    - Graceful degradation on component failures
    """

    def __init__(self, workspace_id: str = "default"):
        self.workspace_id = workspace_id
        self.collector = MetricsCollector()
        self.alert_engine = AlertEngine()

    async def run_async(
        self,
        log_to_mlflow: bool = True,
        correlation_id: Optional[str] = None,
    ) -> MonitoringRunResult:
        """
        Async: Execute the complete monitoring pipeline.

        ✅ FIXED: Non-blocking drift detection + input validation + graceful degradation.
        """
        run_id = str(uuid.uuid4())[:8]
        corr_id = correlation_id or generate_monitoring_correlation_id("pipeline")

        # ✅ Validate inputs
        is_valid, error = _validate_pipeline_inputs(log_to_mlflow, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid pipeline inputs: {error}")
            return MonitoringRunResult(
                run_id=run_id,
                workspace_id=self.workspace_id,
                started_at=datetime.now(timezone.utc).isoformat(),
                error=f"Invalid inputs: {error}",
                correlation_id=corr_id,
            )

        result = MonitoringRunResult(
            run_id=run_id,
            workspace_id=self.workspace_id,
            started_at=datetime.now(timezone.utc).isoformat(),
            correlation_id=corr_id,
        )

        logger.info(f"[{corr_id}] Monitoring pipeline starting: " f"workspace={self.workspace_id} | run_id={run_id}")

        try:
            # -- Step 1: Window statistics -------------------------------------
            try:
                window_stats = await asyncio.wait_for(
                    self.collector.compute_window_stats_async(
                        hours=24.0,
                        workspace_id=self.workspace_id,
                        correlation_id=corr_id,
                    ),
                    timeout=_STEP_TIMEOUT,
                )
                result.window_stats = window_stats
                await self.collector.record_daily_stats_async(window_stats)

                logger.info(
                    f"[{corr_id}] Window stats: queries={window_stats.get('query_count', 0)} | "
                    f"confidence={window_stats.get('confidence_mean', 0):.3f} | "
                    f"latency_p95={window_stats.get('latency_ms_p95', 0):.0f}ms"
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Window stats computation timed out after {_STEP_TIMEOUT}s")
                result.window_stats = {"error": "timeout"}
            except Exception as e:
                logger.error(f"[{corr_id}] Window stats computation failed: {e}")
                result.window_stats = {"error": str(e)}

            if result.window_stats.get("query_count", 0) == 0:
                logger.info(f"[{corr_id}] No queries in window — skipping drift analysis")
                result.completed_at = datetime.now(timezone.utc).isoformat()
                return result

            # -- Step 2: Evidently drift detection (non-blocking) --------------
            try:
                monitor = EvidentlyMonitor(workspace_id=self.workspace_id)
                import sys

                if sys.version_info >= (3, 9):
                    drift_report = await asyncio.wait_for(
                        asyncio.to_thread(
                            lambda: monitor.run(
                                log_to_mlflow=log_to_mlflow,
                                correlation_id=corr_id,
                            )
                        ),
                        timeout=_STEP_TIMEOUT,
                    )
                else:
                    loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                    drift_report = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: monitor.run(
                                log_to_mlflow=log_to_mlflow,
                                correlation_id=corr_id,
                            ),
                        ),
                        timeout=_STEP_TIMEOUT,
                    )
                result.drift_report = drift_report
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Drift detection timed out after {_STEP_TIMEOUT}s")
                result.drift_report = DriftReport(
                    run_date=datetime.now(timezone.utc).isoformat(),
                    workspace_id=self.workspace_id,
                    n_current=0,
                    n_reference=0,
                    drift_detected=False,
                    quality_alerts=["Drift detection timed out"],
                    correlation_id=corr_id,
                )
            except Exception as e:
                logger.error(f"[{corr_id}] Drift detection failed: {e}")
                result.drift_report = DriftReport(
                    run_date=datetime.now(timezone.utc).isoformat(),
                    workspace_id=self.workspace_id,
                    n_current=0,
                    n_reference=0,
                    drift_detected=False,
                    quality_alerts=[f"Drift detection failed: {e}"],
                    correlation_id=corr_id,
                )

            # -- Step 3: Alert check -------------------------------------------
            try:
                if result.drift_report and result.drift_report.quality_alerts:
                    alerts = self.alert_engine.check_and_send(
                        metrics=result.window_stats,
                        domain=self.workspace_id,
                        run_id=result.drift_report.mlflow_run_id if result.drift_report else "",
                        correlation_id=corr_id,
                    )
                    result.alerts_sent = [a.message for a in alerts]
            except Exception as e:
                logger.warning(f"[{corr_id}] Alert check failed: {e}")
                result.alerts_sent.append(f"Alert check failed: {e}")

            # -- Step 4: Auto-improvement --------------------------------------
            try:
                improver = AutoImprover(workspace_id=self.workspace_id)

                if result.drift_report and improver.should_trigger(result.drift_report.quality_alerts):
                    action_type = improver.determine_action(
                        quality_alerts=result.drift_report.quality_alerts,
                        drifted_columns=result.drift_report.drifted_columns if result.drift_report else [],
                        current_stats=result.window_stats,
                    )
                    logger.info(
                        f"[{corr_id}] Auto-improvement triggered: {action_type} | "
                        f"alerts={result.drift_report.quality_alerts[:2] if result.drift_report else []}"
                    )
                    improvement = await improver.execute_async(
                        action_type=action_type,
                        quality_alerts=result.drift_report.quality_alerts if result.drift_report else [],
                        correlation_id=corr_id,
                    )
                    result.improvement = improvement
            except Exception as e:
                logger.warning(f"[{corr_id}] Auto-improvement failed: {e}")

        except Exception as e:
            logger.error(f"[{corr_id}] Monitoring pipeline failed: {e}", exc_info=True)
            result.error = str(e)

        result.completed_at = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"[{corr_id}] Monitoring pipeline complete: run_id={run_id} | "
            f"healthy={result.is_healthy} | "
            f"alerts={len(result.alerts_sent)} | "
            f"improvement={result.improvement.action_type if result.improvement else 'none'}"
        )
        return result

    def run(self, *args, **kwargs) -> MonitoringRunResult:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_run():
            return await self.run_async(*args, **kwargs)

        return run_async_in_task(_do_run)


def get_pipeline_metadata() -> dict[str, Any]:
    """✅ NEW: Return pipeline metadata for debugging."""
    return {
        "step_timeout_seconds": _STEP_TIMEOUT,
        "default_hours": 24.0,
        "components": [
            "MetricsCollector",
            "EvidentlyMonitor",
            "AlertEngine",
            "AutoImprover",
        ],
        "async_safe": True,
        "graceful_degradation": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "MonitoringPipeline",
    "MonitoringRunResult",
    "get_pipeline_metadata",
]
# Local smoke test entry point. Run: python -m

