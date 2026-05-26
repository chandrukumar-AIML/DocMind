# backend/app/api/routes/monitoring.py
# DVMELTSS-FIX: M/E/S + ASCALE-A/E + OWASP-3
# ✅ FIXED: Input validation + proper background task handling + timeout handling

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks, status
from pydantic import BaseModel, Field

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_admin, AuthenticatedUser
from app.models import ErrorResponse
from app.monitoring.metrics_collector import MetricsCollector, QueryMetrics
from app.monitoring.pipeline import MonitoringPipeline, AutoImprover

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/monitoring", tags=["monitoring"])

# ✅ NEW: Operation timeouts (seconds)
_COLLECTOR_TIMEOUT: Final = 30.0
_PIPELINE_TIMEOUT: Final = 300.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class RecordMetricsRequest(BaseModel):
    query_id: Optional[str] = Field(default=None, max_length=64)
    latency_ms: float = Field(..., ge=0)
    retrieval_count: int = Field(default=0, ge=0)
    reranked_count: int = Field(default=0, ge=0)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    is_grounded: bool = True
    needs_human_review: bool = False
    crag_action: str = Field(default="generate")
    retrieval_mode: str = Field(default="vector")
    retry_count: int = Field(default=0, ge=0)
    web_search_used: bool = False
    answer_length: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    faithfulness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    answer_relevancy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    context_precision: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ✅ NEW: Input validation helper
def _validate_monitoring_inputs(
    hours: Optional[float],
    days: Optional[int],
    query_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate monitoring endpoint inputs before processing."""
    if hours is not None and (not isinstance(hours, (int, float)) or hours < 1.0 or hours > 168.0):
        return False, "hours must be between 1.0 and 168.0"
    if days is not None and (not isinstance(days, int) or days < 1 or days > 90):
        return False, "days must be between 1 and 90"
    if query_id is not None and not isinstance(query_id, str):
        return False, "query_id must be a string or None"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "/record",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Record query metrics for monitoring",
)
async def record_query_metrics(
    request: RecordMetricsRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    corr_id = generate_correlation_id("record_metrics")
    
    # ✅ Validate inputs
    is_valid, error = _validate_monitoring_inputs(None, None, request.query_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    collector = MetricsCollector()
    metrics = QueryMetrics(
        query_id=request.query_id or str(uuid.uuid4())[:8],
        workspace_id=user.workspace_id,
        timestamp=time.time(),
        latency_ms=request.latency_ms,
        retrieval_count=request.retrieval_count,
        reranked_count=request.reranked_count,
        confidence_score=request.confidence_score,
        relevance_score=request.relevance_score,
        is_grounded=request.is_grounded,
        needs_human_review=request.needs_human_review,
        crag_action=request.crag_action,
        retrieval_mode=request.retrieval_mode,
        retry_count=request.retry_count,
        web_search_used=request.web_search_used,
        answer_length=request.answer_length,
        citation_count=request.citation_count,
        faithfulness=request.faithfulness,
        answer_relevancy=request.answer_relevancy,
        context_precision=request.context_precision,
    )
    
    try:
        await asyncio.wait_for(
            collector.record_async(metrics),
            timeout=_COLLECTOR_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{corr_id}] Metrics recording timed out after {_COLLECTOR_TIMEOUT}s")
        # Still return success to avoid blocking client
    except Exception as e:
        logger.error(f"[{corr_id}] Metrics recording failed: {e}")
        # Still return success to avoid blocking client
    
    return {
        "status": "recorded",
        "query_id": metrics.query_id,
        "workspace_id": user.workspace_id,
        "correlation_id": corr_id,
    }


@router.get(
    "/stats",
    summary="Get rolling window statistics",
)
async def get_monitoring_stats(
    hours: float = Query(default=24.0, ge=1.0, le=168.0),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> dict:
    corr_id = generate_correlation_id("monitoring_stats")
    workspace_id = user.workspace_id if user else "default"
    
    # ✅ Validate inputs
    is_valid, error = _validate_monitoring_inputs(hours, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    collector = MetricsCollector()
    
    try:
        stats = await asyncio.wait_for(
            collector.compute_window_stats_async(
                hours=hours,
                workspace_id=workspace_id,
                correlation_id=corr_id,
            ),
            timeout=_COLLECTOR_TIMEOUT,
        )
        
        return {
            "workspace_id": workspace_id,
            "correlation_id": corr_id,
            "hours": hours,
            "stats": stats or {},
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Stats computation timed out after {_COLLECTOR_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Stats computation timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Stats computation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute stats")


@router.get(
    "/trend",
    summary="Get daily metric trends for the last N days",
)
async def get_metric_trend(
    days: int = Query(default=30, ge=1, le=90),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> dict:
    corr_id = generate_correlation_id("metric_trend")
    workspace_id = user.workspace_id if user else "default"
    
    # ✅ Validate inputs
    is_valid, error = _validate_monitoring_inputs(None, days, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    collector = MetricsCollector()
    
    try:
        try:
            trend_coro = collector.get_daily_trend_async(
                days=days,
                workspace_id=workspace_id,
                correlation_id=corr_id,
            )
        except TypeError:
            trend_coro = collector.get_daily_trend_async(days=days)
        trend = await asyncio.wait_for(trend_coro, timeout=_COLLECTOR_TIMEOUT)
        
        return {
            "workspace_id": workspace_id,
            "correlation_id": corr_id,
            "days": days,
            "trend": trend or [],
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Trend computation timed out after {_COLLECTOR_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Trend computation timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Trend computation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to compute trend")


@router.post(
    "/run",
    summary="Trigger the full monitoring pipeline",
)
async def run_monitoring_pipeline(
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
    background_tasks: BackgroundTasks,
    async_mode: bool = Query(default=True, description="True = fire-and-forget"),
) -> dict:
    corr_id = generate_correlation_id("monitoring_pipeline")
    
    pipeline = MonitoringPipeline(workspace_id=user.workspace_id)
    
    if async_mode:
        # ✅ FIXED: Use sync function for background task
        def _run_sync():
            try:
                asyncio.run(pipeline.run(log_to_mlflow=True, correlation_id=corr_id))
            except Exception as e:
                logger.error(f"[{corr_id}] Monitoring pipeline failed: {e}", exc_info=True)
                
        background_tasks.add_task(_run_sync)
        return {
            "status": "started",
            "workspace_id": user.workspace_id,
            "message": "Monitoring pipeline running in background. Check MLflow for results.",
            "correlation_id": corr_id,
        }
    else:
        try:
            result = await asyncio.wait_for(
                pipeline.run(log_to_mlflow=True, correlation_id=corr_id),
                timeout=_PIPELINE_TIMEOUT,
            )
            return {
                "run_id": getattr(result, "run_id", None),
                "is_healthy": getattr(result, "is_healthy", False),
                "window_stats": getattr(result, "window_stats", {}),
                "drift_detected": getattr(getattr(result, "drift_report", None), "drift_detected", False) if result else False,
                "alerts_sent": getattr(result, "alerts_sent", []),
                "improvement": getattr(getattr(result, "improvement", None), "action_type", None) if result else None,
                "correlation_id": corr_id,
            }
        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Pipeline timed out after {_PIPELINE_TIMEOUT}s")
            raise HTTPException(status_code=408, detail="Pipeline execution timed out")
        except Exception as e:
            logger.error(f"[{corr_id}] Sync pipeline failed: {e}")
            raise HTTPException(status_code=500, detail="Pipeline execution failed")


@router.post(
    "/rechunk",
    summary="Manually trigger rechunking for degraded documents",
)
async def trigger_rechunk(
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
) -> dict:
    corr_id = generate_correlation_id("rechunk_trigger")
    
    try:
        improver = AutoImprover(workspace_id=user.workspace_id)
        
        # ✅ FIXED: Use async method if available
        if hasattr(improver, "execute_async"):
            action = await asyncio.wait_for(
                improver.execute_async(
                    action_type="rechunk",
                    quality_alerts=["manual_trigger"],
                    correlation_id=corr_id,
                ),
                timeout=_PIPELINE_TIMEOUT,
            )
        else:
            # Fallback: run in executor
            import functools
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            action = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    functools.partial(
                        improver.execute,
                        action_type="rechunk",
                        quality_alerts=["manual_trigger"],
                        correlation_id=corr_id,
                    ),
                ),
                timeout=_PIPELINE_TIMEOUT,
            )
        
        return {
            "action_type": getattr(action, "action_type", None),
            "success": getattr(action, "success", False),
            "parameters": getattr(action, "parameters", {}),
            "error": getattr(action, "error", None),
            "workspace_id": user.workspace_id,
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Rechunk timed out after {_PIPELINE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Rechunk operation timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Rechunk failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Rechunk operation failed")


def get_monitoring_metadata() -> dict[str, Any]:
    """✅ NEW: Return monitoring API metadata for debugging."""
    return {
        "endpoints": [
            "/monitoring/record",
            "/monitoring/stats",
            "/monitoring/trend",
            "/monitoring/run",
            "/monitoring/rechunk",
        ],
        "timeouts": {
            "collector_seconds": _COLLECTOR_TIMEOUT,
            "pipeline_seconds": _PIPELINE_TIMEOUT,
        },
        "limits": {
            "stats_hours_min": 1.0,
            "stats_hours_max": 168.0,
            "trend_days_min": 1,
            "trend_days_max": 90,
        },
        "workspace_scoped": True,
        "mlflow_integration": True,
        "auto_improvement_enabled": True,
    }


__all__ = ["router", "get_monitoring_metadata"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

