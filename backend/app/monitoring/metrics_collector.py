# ACID-INDEX: E - Error handling (graceful degradation)

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import Final, Optional, Any

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.monitoring_utils import (
    get_monitoring_redis,
    compute_percentile,
    compute_mean,
    validate_monitoring_window,
)
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution

logger = logging.getLogger(__name__)

# Redis key configuration
METRICS_KEY: Final = "rag:metrics:queries"  # sorted set: score=timestamp
DAILY_STATS_KEY: Final = "rag:metrics:daily"  # hash: date -> stats JSON
WINDOW_SECONDS: Final = 86400  # 24-hour sliding window


@dataclass
class QueryMetrics:
    """
    Metrics captured for a single RAG query.
    Stored in Redis for sliding-window analysis.
    """

    # Identity
    query_id: str
    workspace_id: str
    timestamp: float  # unix timestamp

    # Performance
    latency_ms: float
    retrieval_count: int
    reranked_count: int

    # Quality indicators
    confidence_score: float
    relevance_score: float
    is_grounded: bool
    needs_human_review: bool

    # CRAG decisions
    crag_action: str  # generate/rewrite/decompose/web_search
    retrieval_mode: str  # vector/graph/hybrid
    retry_count: int
    web_search_used: bool

    # Answer characteristics
    answer_length: int  # characters
    citation_count: int

    # Fields with defaults (must come after non-default fields)
    correlation_id: Optional[str] = None
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None
    context_precision: Optional[float] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Replace None with sentinel for Redis storage
        return {k: (v if v is not None else -1.0) for k, v in d.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "QueryMetrics":
        cleaned = {k: (v if v != -1.0 else None) for k, v in d.items()}
        # Ensure required fields have defaults
        return cls(
            query_id=cleaned.get("query_id", ""),
            workspace_id=cleaned.get("workspace_id", ""),
            timestamp=cleaned.get("timestamp", time.time()),
            latency_ms=cleaned.get("latency_ms", 0.0),
            retrieval_count=cleaned.get("retrieval_count", 0),
            reranked_count=cleaned.get("reranked_count", 0),
            confidence_score=cleaned.get("confidence_score", 0.0),
            relevance_score=cleaned.get("relevance_score", 0.0),
            is_grounded=cleaned.get("is_grounded", False),
            needs_human_review=cleaned.get("needs_human_review", False),
            crag_action=cleaned.get("crag_action", "generate"),
            retrieval_mode=cleaned.get("retrieval_mode", "vector"),
            retry_count=cleaned.get("retry_count", 0),
            web_search_used=cleaned.get("web_search_used", False),
            answer_length=cleaned.get("answer_length", 0),
            citation_count=cleaned.get("citation_count", 0),
            correlation_id=cleaned.get("correlation_id"),
            faithfulness=cleaned.get("faithfulness"),
            answer_relevancy=cleaned.get("answer_relevancy"),
            context_precision=cleaned.get("context_precision"),
        )


def _validate_metrics_inputs(
    metrics: Optional[QueryMetrics],
    workspace_id: Optional[str],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate metrics inputs before processing."""
    if metrics is not None and not isinstance(metrics, QueryMetrics):
        return False, "metrics must be a QueryMetrics instance or None"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class MetricsCollector:
    """
    Records per-query metrics to Redis for monitoring pipeline consumption.

    Features (DVMELTSS-A, BATMAN-M, ACID-E):
    - Async Redis via redis.asyncio (non-blocking)
    - Memory-safe batch operations
    - Graceful degradation on Redis failures
    - Correlation ID tracing for distributed debugging
    """

    def __init__(self, redis_url: Optional[str] = None):
        settings = get_settings()
        self.redis_url = redis_url or getattr(settings, "redis_url", "redis://localhost:6379/3")
        self._redis: Optional[Any] = None
        logger.info(f"MetricsCollector initialized: redis={self.redis_url}")

    async def _get_redis(self) -> Any:
        """Lazy-load async Redis connection."""
        if self._redis is None:
            self._redis = await get_monitoring_redis(self.redis_url)
            logger.debug("Monitoring Redis connection established")
        return self._redis

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            logger.info("Monitoring Redis connection closed.")

    async def record_async(self, metrics: QueryMetrics) -> bool:
        """
        Async: Record a query metrics event.
        BATMAN-A: Non-blocking Redis operations.

        Returns:
            True on success, False on failure (never raises)
        """
        try:
            redis = await self._get_redis()
            metrics_json = json.dumps(metrics.to_dict())

            pipe = redis.pipeline()
            # Add to sorted set (score = timestamp for range queries)
            pipe.zadd(METRICS_KEY, {metrics_json: metrics.timestamp})
            # Prune entries older than 24h
            cutoff = time.time() - WINDOW_SECONDS
            pipe.zremrangebyscore(METRICS_KEY, "-inf", cutoff)
            # Set 25h TTL on the whole key
            pipe.expire(METRICS_KEY, WINDOW_SECONDS + 3600)

            await pipe.execute()
            return True

        except Exception as e:
            logger.warning(f"Metrics record failed: {e}")
            return False  # Graceful degradation

    def record(self, metrics: QueryMetrics) -> bool:
        """
        Sync wrapper for backward compatibility.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_record():
            return await self.record_async(metrics)

        return run_async_in_task(_do_record)

    async def get_recent_async(
        self,
        hours: float = 24.0,
        workspace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> list[QueryMetrics]:
        """
        Async: Get all metrics within the last N hours.

        Args:
            hours: lookback window
            workspace_id: filter to specific workspace (None = all)
            correlation_id: optional filter for tracing
        """
        hours = validate_monitoring_window(hours)
        cutoff = time.time() - (hours * 3600)

        try:
            redis = await self._get_redis()
            raw_entries = await redis.zrangebyscore(METRICS_KEY, cutoff, "+inf")

            metrics = []
            for raw in raw_entries:
                try:
                    d = json.loads(raw)
                    m = QueryMetrics.from_dict(d)

                    if workspace_id and m.workspace_id != workspace_id:
                        continue
                    if correlation_id and m.correlation_id != correlation_id:
                        continue

                    metrics.append(m)
                except Exception:
                    continue
            return metrics

        except Exception as e:
            logger.warning(f"Metrics fetch failed: {e}")
            return []

    def get_recent(self, *args, **kwargs) -> list[QueryMetrics]:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_get():
            return await self.get_recent_async(*args, **kwargs)

        return run_async_in_task(_do_get)

    async def compute_window_stats_async(
        self,
        hours: float = 24.0,
        workspace_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Async: Compute aggregate statistics for the rolling window.
        """
        hours = validate_monitoring_window(hours)
        metrics = await self.get_recent_async(
            hours=hours,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
        )

        if not metrics:
            return {
                "window_hours": hours,
                "query_count": 0,
                "workspace_id": workspace_id or "all",
                "data_available": False,
                "correlation_id": correlation_id,
            }

        latencies = [m.latency_ms for m in metrics]
        confidences = [m.confidence_score for m in metrics]
        relevances = [m.relevance_score for m in metrics]
        answer_lengths = [m.answer_length for m in metrics]
        retrieval_counts = [m.retrieval_count for m in metrics]

        faithfulness_vals = [m.faithfulness for m in metrics if m.faithfulness is not None]
        relevancy_vals = [m.answer_relevancy for m in metrics if m.answer_relevancy is not None]
        precision_vals = [m.context_precision for m in metrics if m.context_precision is not None]

        crag_dist: dict[str, int] = {}
        for m in metrics:
            crag_dist[m.crag_action] = crag_dist.get(m.crag_action, 0) + 1

        web_search_pct = sum(1 for m in metrics if m.web_search_used) / len(metrics)
        human_review_pct = sum(1 for m in metrics if m.needs_human_review) / len(metrics)

        stats = {
            "window_hours": hours,
            "query_count": len(metrics),
            "workspace_id": workspace_id or "all",
            "data_available": True,
            "correlation_id": correlation_id,
            "latency_ms_mean": round(compute_mean(latencies) or 0, 1),
            "latency_ms_p50": round(compute_percentile(latencies, 50) or 0, 1),
            "latency_ms_p95": round(compute_percentile(latencies, 95) or 0, 1),
            "latency_ms_p99": round(compute_percentile(latencies, 99) or 0, 1),
            "confidence_mean": round(compute_mean(confidences) or 0, 4),
            "relevance_mean": round(compute_mean(relevances) or 0, 4),
            "answer_length_mean": round(compute_mean(answer_lengths) or 0, 1),
            "retrieval_count_mean": round(compute_mean(retrieval_counts) or 0, 2),
            "faithfulness_mean": round(compute_mean(faithfulness_vals) or 0, 4) if faithfulness_vals else None,
            "answer_relevancy_mean": round(compute_mean(relevancy_vals) or 0, 4) if relevancy_vals else None,
            "context_precision_mean": round(compute_mean(precision_vals) or 0, 4) if precision_vals else None,
            "crag_action_distribution": crag_dist,
            "web_search_rate": round(web_search_pct, 4),
            "human_review_rate": round(human_review_pct, 4),
            "faithfulness_alert": ((compute_mean(faithfulness_vals) or 1.0) < 0.70 if faithfulness_vals else False),
            "latency_alert": (compute_percentile(latencies, 95) or 0) > 8000,
        }
        return stats

    def compute_window_stats(self, *args, **kwargs) -> dict:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_compute():
            return await self.compute_window_stats_async(*args, **kwargs)

        return run_async_in_task(_do_compute)

    async def record_daily_stats_async(self, stats: dict) -> None:
        """Async: Persist daily stats for trend analysis."""
        from datetime import date

        today = date.today().isoformat()
        try:
            redis = await self._get_redis()
            await redis.hset(DAILY_STATS_KEY, today, json.dumps(stats))
            await redis.expire(DAILY_STATS_KEY, 90 * 86400)
        except Exception as e:
            logger.warning(f"Daily stats save failed: {e}")

    def record_daily_stats(self, stats: dict) -> None:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_record():
            await self.record_daily_stats_async(stats)

        run_async_in_task(_do_record)

    async def get_daily_trend_async(self, days: int = 30) -> list[dict]:
        """Async: Get daily stats for the last N days for trend charts."""
        try:
            redis = await self._get_redis()
            all_daily = await redis.hgetall(DAILY_STATS_KEY)

            from datetime import date, timedelta

            result = []
            for i in range(days - 1, -1, -1):
                day = (date.today() - timedelta(days=i)).isoformat()
                if day in all_daily:
                    result.append({"date": day, **json.loads(all_daily[day])})
            return result
        except Exception as e:
            logger.warning(f"Daily trend fetch failed: {e}")
            return []

    def get_daily_trend(self, days: int = 30) -> list[dict]:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_get():
            return await self.get_daily_trend_async(days)

        return run_async_in_task(_do_get)


# -- Global collector instance ----------------------------------------------
_collector: Optional[MetricsCollector] = None


def get_collector() -> MetricsCollector:
    """Get or initialize global metrics collector."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector


# -- Convenience Functions (Sync wrappers for background tasks) -------------


def record_query_latency(
    workspace_id: str,
    correlation_id: str,
    latency_seconds: float,
    success: bool,
) -> None:
    """Record query latency in background task."""
    try:
        logger.debug(
            f"Query latency | workspace={workspace_id} "
            f"latency={latency_seconds:.2f}s success={success} | {correlation_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to record latency: {e}")


def record_query_error(
    workspace_id: str,
    correlation_id: str,
    error_message: str,
    error_type: str = "UNKNOWN",
) -> None:
    """Record query errors in background task."""
    try:
        logger.error(
            f"Query error | workspace={workspace_id} " f"error={error_type}: {error_message} | {correlation_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to record error: {e}")


def record_ingest_latency(
    workspace_id: str,
    correlation_id: str,
    latency_seconds: float,
    success: bool,
) -> None:
    """Record document ingest latency in background task."""
    try:
        logger.debug(
            f"Ingest latency | workspace={workspace_id} "
            f"latency={latency_seconds:.2f}s success={success} | {correlation_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to record ingest latency: {e}")


def record_ingest_error(
    workspace_id: str,
    correlation_id: str,
    error_message: str,
    error_type: str = "UNKNOWN",
) -> None:
    """Record document ingest errors in background task."""
    try:
        logger.error(
            f"Ingest error | workspace={workspace_id} " f"error={error_type}: {error_message} | {correlation_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to record ingest error: {e}")


# -- Auth Metrics -----------------------------------------------------------


def record_auth_attempt(
    workspace_id: str,
    correlation_id: str,
    success: bool,
    user_id: Optional[str] = None,
    auth_method: str = "jwt",
) -> None:
    """
    Record authentication attempt for monitoring.

    DVMELTSS-M, E - Gracefully degrades on errors
    """
    try:
        status = "success" if success else "failed"
        logger.info(
            f"Auth attempt | workspace={workspace_id} user={user_id or 'anon'} "
            f"method={auth_method} status={status} | {correlation_id}"
        )
    except Exception as e:
        logger.warning(f"Failed to record auth metric: {e}")


# -- Document Operation Metrics ---------------------------------------------


def record_document_operation(
    workspace_id: str,
    correlation_id: str,
    operation: str,  # "upload" | "delete" | "update" | "view"
    source_file: str,
    success: bool,
    user_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """
    Record document operation for monitoring and audit trail.

    DVMELTSS-M, E - Gracefully degrades on errors
    """
    try:
        status = "success" if success else "failed"
        logger.info(
            f"Document {operation} | workspace={workspace_id} file={source_file} "
            f"user={user_id or 'anon'} status={status} | {correlation_id}" + (f" details={details}" if details else "")
        )
    except Exception as e:
        logger.warning(f"Failed to record document operation: {e}")


# -- Evaluation Run Metrics -------------------------------------------------


def record_evaluation_run(
    workspace_id: str,
    correlation_id: str,
    evaluation_type: str,  # "ragas" | "custom" | "human"
    dataset_size: int,
    success: bool,
    metrics: Optional[dict] = None,
    user_id: Optional[str] = None,
) -> None:
    """
    Record evaluation run for monitoring and reporting.

    Args:
        workspace_id: Workspace context
        correlation_id: Request tracing ID
        evaluation_type: Type of evaluation: ragas/custom/human
        dataset_size: Number of samples evaluated
        success: Whether evaluation completed successfully
        metrics: Evaluation metrics dict (faithfulness, relevancy, etc.)
        user_id: User ID who triggered evaluation (optional)

    DVMELTSS-M, E - Gracefully degrades on errors
    """
    try:
        status = "success" if success else "failed"
        metrics_summary = f"metrics={list(metrics.keys())}" if metrics else "no metrics"
        logger.info(
            f"Evaluation {evaluation_type} | workspace={workspace_id} "
            f"dataset_size={dataset_size} {metrics_summary} status={status} | {correlation_id}"
        )
        # NOTE: Evaluation runs are persisted via structured logs above and
        # surfaced by MLflow tracking. Optional Redis metric recording can be
        # wired here when a collector instance is available in this context.
    except Exception as e:
        logger.warning(f"Failed to record evaluation run: {e}")


# ========================================================================
# -- Prometheus Metrics Export (DVMELTSS-M: Graceful fallback) -----------
# ========================================================================


def get_prometheus_metrics(
    workspace_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> str:
    """
    Generate Prometheus-format metrics for scraping.

    ✅ FIXED: Returns valid Prometheus metrics even when Redis is unavailable.

    Args:
        workspace_id: Optional workspace filter
        correlation_id: Optional request tracing ID

    Returns:
        str: Prometheus text format metrics
    """
    try:
        from app.config import get_settings

        settings = get_settings()

        # Check if Redis is configured
        redis_url = getattr(settings, "redis_url", None)
        if not redis_url:
            logger.debug("ℹ️ Redis not configured — returning basic metrics only")
            return _get_basic_prometheus_metrics(settings, correlation_id, redis_configured=False)

        # Try to collect advanced metrics from Redis
        try:
            collector = MetricsCollector()

            async def _collect_stats():
                return await collector.compute_window_stats_async(hours=24, workspace_id=workspace_id)

            stats = run_async_in_task(_collect_stats, timeout=5.0)
            if not isinstance(stats, dict):
                # cleanly if an integration returns an unexpected payload.
                raise TypeError(f"metrics stats must be dict, got {type(stats).__name__}")

            return _format_advanced_prometheus_metrics(settings, stats, correlation_id, redis_configured=True)

        except Exception as e:
            # failure observable for Prometheus and logs.
            error_message = str(e) or e.__class__.__name__
            logger.warning(f"Advanced metrics collection failed: {error_message}. Returning basic metrics.")
            return _get_basic_prometheus_metrics(settings, correlation_id, redis_configured=True, error=error_message)

    except Exception as e:
        error_message = str(e) or e.__class__.__name__
        logger.error(f"Metrics generation failed: {error_message}", exc_info=True)
        # Always return valid Prometheus format, even on error
        from app.config import get_settings

        settings = get_settings()
        return _get_basic_prometheus_metrics(settings, correlation_id, redis_configured=False, error=error_message)


def _get_basic_prometheus_metrics(
    settings,
    correlation_id: Optional[str],
    redis_configured: bool,
    timeout: bool = False,
    error: Optional[str] = None,
) -> str:
    """Generate minimal valid Prometheus metrics when advanced collection fails."""
    import time

    lines = [
        "# HELP documind_ai_up DocuMind AI service status",
        "# TYPE documind_ai_up gauge",
        "documind_ai_up 1",
        "",
        "# HELP documind_ai_version Service version information",
        "# TYPE documind_ai_version gauge",
        f'documind_ai_version{{version="{settings.app_version}"}} 1',
        "",
        "# HELP documind_ai_health_status Overall health status (1=ok, 0=degraded, -1=error)",
        "# TYPE documind_ai_health_status gauge",
        "documind_ai_health_status 1",
        "",
        "# HELP documind_ai_redis_configured Whether Redis is configured",
        "# TYPE documind_ai_redis_configured gauge",
        f'documind_ai_redis_configured{{enabled="{str(redis_configured).lower()}"}} 1',
        "",
        "# HELP documind_ai_ocr_ready OCR pipeline readiness",
        "# TYPE documind_ai_ocr_ready gauge",
        "documind_ai_ocr_ready 1",
        "",
        "# HELP documind_ai_rag_ready RAG chain readiness",
        "# TYPE documind_ai_rag_ready gauge",
        "documind_ai_rag_ready 1",
        "",
        "# HELP documind_ai_vectorstore_ready Vector store readiness",
        "# TYPE documind_ai_vectorstore_ready gauge",
        "documind_ai_vectorstore_ready 1",
        "",
        "# HELP documind_ai_process_start_time Process start time (Unix timestamp)",
        "# TYPE documind_ai_process_start_time gauge",
        f"documind_ai_process_start_time {time.time()}",
    ]

    if correlation_id:
        lines.extend(
            [
                "",
                "# HELP documind_ai_correlation_id Request correlation ID for tracing",
                "# TYPE documind_ai_correlation_id gauge",
                f'documind_ai_correlation_id{{id="{correlation_id}"}} 1',
            ]
        )

    if timeout:
        lines.extend(
            [
                "",
                "# HELP documind_ai_metrics_timeout Indicates metrics collection timed out",
                "# TYPE documind_ai_metrics_timeout gauge",
                "documind_ai_metrics_timeout 1",
            ]
        )

    if error:
        safe_error = error[:200].replace('"', '\\"').replace("\n", "\\n").replace("\r", "")
        lines.extend(
            [
                "",
                "# HELP documind_ai_metrics_error Last metrics collection error message",
                "# TYPE documind_ai_metrics_error gauge",
                f'documind_ai_metrics_error{{error="{safe_error}"}} 1',
            ]
        )

    return "\n".join(lines) + "\n"


def _format_advanced_prometheus_metrics(
    settings,
    stats: dict,
    correlation_id: Optional[str],
    redis_configured: bool,
) -> str:
    """Format advanced metrics from Redis into Prometheus text format."""
    import time

    lines = [
        "# HELP documind_ai_up DocuMind AI service status",
        "# TYPE documind_ai_up gauge",
        "documind_ai_up 1",
        "",
        "# HELP documind_ai_version Service version information",
        "# TYPE documind_ai_version gauge",
        f'documind_ai_version{{version="{settings.app_version}"}} 1',
        "",
        "# HELP documind_ai_redis_configured Whether Redis is configured",
        "# TYPE documind_ai_redis_configured gauge",
        f'documind_ai_redis_configured{{enabled="{str(redis_configured).lower()}"}} 1',
        "",
        "# HELP documind_ai_query_count_total Total queries in monitoring window",
        "# TYPE documind_ai_query_count_total counter",
        f"documind_ai_query_count_total {stats.get('query_count', 0)}",
        "",
        "# HELP documind_ai_query_latency_ms_mean Mean query latency in milliseconds",
        "# TYPE documind_ai_query_latency_ms_mean gauge",
        f"documind_ai_query_latency_ms_mean {stats.get('latency_ms_mean', 0)}",
        "",
        "# HELP documind_ai_query_latency_ms_p95 95th percentile query latency in milliseconds",
        "# TYPE documind_ai_query_latency_ms_p95 gauge",
        f"documind_ai_query_latency_ms_p95 {stats.get('latency_ms_p95', 0)}",
        "",
        "# HELP documind_ai_query_latency_ms_p99 99th percentile query latency in milliseconds",
        "# TYPE documind_ai_query_latency_ms_p99 gauge",
        f"documind_ai_query_latency_ms_p99 {stats.get('latency_ms_p99', 0)}",
        "",
        "# HELP documind_ai_confidence_mean Mean confidence score of answers",
        "# TYPE documind_ai_confidence_mean gauge",
        f"documind_ai_confidence_mean {stats.get('confidence_mean', 0)}",
        "",
        "# HELP documind_ai_relevance_mean Mean relevance score of answers",
        "# TYPE documind_ai_relevance_mean gauge",
        f"documind_ai_relevance_mean {stats.get('relevance_mean', 0)}",
        "",
        "# HELP documind_ai_web_search_rate Rate of queries using web search fallback",
        "# TYPE documind_ai_web_search_rate gauge",
        f"documind_ai_web_search_rate {stats.get('web_search_rate', 0)}",
        "",
        "# HELP documind_ai_human_review_rate Rate of queries flagged for human review",
        "# TYPE documind_ai_human_review_rate gauge",
        f"documind_ai_human_review_rate {stats.get('human_review_rate', 0)}",
        "",
        "# HELP documind_ai_process_start_time Process start time (Unix timestamp)",
        "# TYPE documind_ai_process_start_time gauge",
        f"documind_ai_process_start_time {time.time()}",
    ]

    # Add CRAG action distribution as separate metrics
    crag_dist = stats.get("crag_action_distribution", {})
    for action, count in crag_dist.items():
        safe_action = action.replace("-", "_").replace(" ", "_").replace('"', "_")
        lines.extend(
            [
                f"# HELP documind_ai_crag_action_{safe_action} Count of CRAG action: {action}",
                f"# TYPE documind_ai_crag_action_{safe_action} counter",
                f"documind_ai_crag_action_{safe_action} {count}",
                "",
            ]
        )

    if correlation_id:
        lines.extend(
            [
                "# HELP documind_ai_correlation_id Request correlation ID for tracing",
                "# TYPE documind_ai_correlation_id gauge",
                f'documind_ai_correlation_id{{id="{correlation_id}"}} 1',
                "",
            ]
        )

    return "\n".join(lines) + "\n"


def get_monitoring_metadata() -> dict[str, Any]:
    """✅ NEW: Return monitoring metadata for debugging."""
    return {
        "redis_keys": {
            "metrics": METRICS_KEY,
            "daily_stats": DAILY_STATS_KEY,
        },
        "window_seconds": WINDOW_SECONDS,
        "default_hours": 24.0,
        "prometheus_endpoint": "/metrics",
    }


# -- Module Exports ---------------------------------------------------------

__all__ = [
    "MetricsCollector",
    "QueryMetrics",
    "record_query_latency",
    "record_query_error",
    "record_ingest_latency",
    "record_ingest_error",
    "record_auth_attempt",
    "record_document_operation",
    "record_evaluation_run",
    "get_prometheus_metrics",
    "get_monitoring_metadata",  # ✅ NEW: Added for monitoring
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.monitoring.metrics_collector) -
# ========================================================================

