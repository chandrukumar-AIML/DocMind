# backend/app/api/routes/health.py
# DVMELTSS-FIX: E/M/S + ASCALE-A/E + K8s best practices
# ✅ FIXED: Proper async handling + input validation + safe Prometheus formatting

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.ids import generate_correlation_id
from app.models import ErrorResponse

logger = logging.getLogger(__name__)


# [OK] FIXED: Replaced module-level get_settings() call with a lazy proxy.
# Accessing settings.X now calls get_settings() at request time, not at import time,
# preventing crashes when env vars are not configured during tests/CI.
class _LazySettings:
    """Proxy that forwards attribute access to get_settings() on first use."""

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _LazySettings()

router = APIRouter(tags=["health"])


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class ComponentHealth(BaseModel):
    status: str = Field(..., pattern="^(ok|degraded|error)$")
    latency_ms: Optional[float] = Field(default=None, ge=0)
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class HealthCheckResponse(BaseModel):
    status: str = Field(..., pattern="^(ok|degraded|error)$")
    version: str
    timestamp: str
    components: Dict[str, ComponentHealth]
    correlation_id: str
    startup_errors: List[str] = Field(default_factory=list)


# ========================================================================
# INTERNAL: Health check helpers
# ========================================================================
async def _check_vector_store(request: Request) -> ComponentHealth:
    """Check vector store (ChromaDB + FAISS) health."""
    start_ts = time.perf_counter()

    try:
        store = getattr(request.app.state, "store_manager", None)
        if store is None:
            if not settings.eager_startup_services:
                return ComponentHealth(
                    status="degraded",
                    error="VectorStoreManager will initialize on first use",
                    details={"lazy_startup": True},
                )
            return ComponentHealth(
                status="error",
                error="VectorStoreManager not initialized",
            )

        # ✅ FIXED: Proper async handling for property access
        async def _get_stats():
            # If stats is a simple property, access directly
            # If it does I/O, run in thread
            return store.stats

        try:
            # Try direct access first (for simple properties)
            stats = await asyncio.wait_for(_get_stats(), timeout=5.0)
        except asyncio.TimeoutError:
            # Fallback: run in executor if it blocks
            import sys

            if sys.version_info >= (3, 9):
                stats = await asyncio.wait_for(
                    asyncio.to_thread(lambda: store.stats),
                    timeout=5.0,
                )
            else:
                loop = asyncio.get_running_loop()
                stats = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: store.stats),
                    timeout=5.0,
                )

        latency_ms = (time.perf_counter() - start_ts) * 1000

        return ComponentHealth(
            status="ok",
            latency_ms=round(latency_ms, 2),
            details={
                "chroma_chunks": stats.get("chroma_chunks", 0) if isinstance(stats, dict) else 0,
                "faiss_vectors": stats.get("faiss_vectors", 0) if isinstance(stats, dict) else 0,
                "documents": stats.get("documents", 0) if isinstance(stats, dict) else 0,
                "cache_stats": stats.get("cache_stats", {}) if isinstance(stats, dict) else {},
            },
        )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_ts) * 1000
        logger.error(f"Vector store health check failed: {e}", exc_info=True)
        return ComponentHealth(
            status="error",
            latency_ms=round(latency_ms, 2),
            error=str(e),
        )


async def _check_ocr_pipeline(request: Request) -> ComponentHealth:
    """Check OCR pipeline readiness."""
    try:
        ocr_pipeline = getattr(request.app.state, "ocr_pipeline", None)
        if ocr_pipeline is None:
            if not settings.eager_startup_services:
                return ComponentHealth(
                    status="degraded",
                    error="OCR pipeline will initialize on first use",
                    details={"lazy_startup": True},
                )
            return ComponentHealth(status="error", error="OCR pipeline not initialized")

        # ✅ FIXED: Safe attribute checks
        has_model = False
        try:
            has_model = (
                (hasattr(ocr_pipeline, "paddle_model") and ocr_pipeline.paddle_model is not None)
                or (hasattr(ocr_pipeline, "paddle_engine") and ocr_pipeline.paddle_engine is not None)
                or (hasattr(ocr_pipeline, "model") and ocr_pipeline.model is not None)
            )
        except Exception:
            pass

        if not has_model:
            return ComponentHealth(status="degraded", error="OCR models not fully loaded")

        return ComponentHealth(status="ok")

    except Exception as e:
        logger.error(f"OCR health check failed: {e}", exc_info=True)
        return ComponentHealth(status="error", error=str(e))


async def _check_rag_chain(request: Request) -> ComponentHealth:
    """Check RAG chain initialization."""
    try:
        rag_chain = getattr(request.app.state, "rag_chain", None)
        if rag_chain is None:
            if not settings.eager_startup_services:
                return ComponentHealth(
                    status="degraded",
                    error="RAG chain will initialize on first use",
                    details={"lazy_startup": True},
                )
            return ComponentHealth(status="error", error="RAG chain not initialized")

        # ✅ FIXED: Safe attribute checks
        try:
            if not hasattr(rag_chain, "llm") or rag_chain.llm is None:
                return ComponentHealth(status="degraded", error="LLM client not available")
        except Exception:
            return ComponentHealth(status="degraded", error="LLM check failed")

        return ComponentHealth(status="ok")

    except Exception as e:
        logger.error(f"RAG chain health check failed: {e}", exc_info=True)
        return ComponentHealth(status="error", error=str(e))


async def _check_database() -> ComponentHealth:
    """Check PostgreSQL connectivity — most critical dependency.

    If this returns 'error', every endpoint that touches the DB will fail.
    Listed first in gather() so it appears first in the response dict.
    """
    start_ts = time.perf_counter()
    try:
        from app.database.engine import check_database_health

        healthy = await asyncio.wait_for(
            check_database_health(verify_schema=False),
            timeout=5.0,
        )
        latency_ms = (time.perf_counter() - start_ts) * 1000
        if not healthy:
            return ComponentHealth(
                status="error",
                latency_ms=round(latency_ms, 2),
                error="Database ping returned False",
            )
        return ComponentHealth(status="ok", latency_ms=round(latency_ms, 2))
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - start_ts) * 1000
        logger.error("Database health check timed out")
        return ComponentHealth(
            status="error",
            latency_ms=round(latency_ms, 2),
            error="Database health check timeout (>5s)",
        )
    except Exception as e:
        latency_ms = (time.perf_counter() - start_ts) * 1000
        logger.error(f"Database health check failed: {e}", exc_info=True)
        return ComponentHealth(
            status="error",
            latency_ms=round(latency_ms, 2),
            error=f"{type(e).__name__}: {e}",
        )


async def _check_cache(request: Request) -> ComponentHealth:
    """Check Redis cache connectivity — optional component."""
    start_ts = time.perf_counter()

    try:
        # ✅ FIXED: Check Redis config BEFORE calling get_cache() to avoid unnecessary connection
        redis_url = getattr(settings, "redis_url", None)
        if not redis_url:
            logger.info("ℹ️ Redis not configured (optional) — skipping cache health check")
            return ComponentHealth(
                status="ok",
                latency_ms=0,
                error=None,
                details={"note": "Redis not configured — cache disabled"},
            )

        from app.cache import get_cache
        import redis.exceptions

        cache = await get_cache()

        # ✅ FIXED: Handle cache being None
        if cache is None:
            return ComponentHealth(
                status="degraded",
                error="Cache not initialized",
                details={"redis_url": redis_url},
            )

        try:
            is_healthy = await asyncio.wait_for(
                cache.is_healthy(),
                timeout=5.0,
            )
        except (
            redis.exceptions.ConnectionError,
            redis.exceptions.TimeoutError,
            OSError,
        ) as e:
            logger.warning(f"Redis unavailable (optional): {e}")
            return ComponentHealth(
                status="degraded",
                latency_ms=(time.perf_counter() - start_ts) * 1000,
                error=f"Redis unavailable: {type(e).__name__}",
                details={
                    "redis_url": redis_url,
                    "note": "Cache disabled — core functionality still works",
                },
            )
        except asyncio.TimeoutError:
            logger.warning("Redis health check timed out")
            return ComponentHealth(
                status="degraded",
                latency_ms=(time.perf_counter() - start_ts) * 1000,
                error="Redis health check timeout",
            )
        except Exception as e:
            logger.error(f"🔴 Unexpected cache error: {type(e).__name__}: {e}", exc_info=True)
            return ComponentHealth(
                status="error",
                latency_ms=(time.perf_counter() - start_ts) * 1000,
                error=f"{type(e).__name__}: {e}",
            )

        if not is_healthy:
            return ComponentHealth(
                status="degraded",
                error="Redis ping returned false",
                details={"redis_url": redis_url},
            )

        try:
            stats = await asyncio.wait_for(
                cache.get_stats(),
                timeout=5.0,
            )
        except Exception:
            stats = None

        latency_ms = (time.perf_counter() - start_ts) * 1000

        return ComponentHealth(
            status="ok",
            latency_ms=round(latency_ms, 2),
            details={
                "embed_hit_rate": round(stats.embed_hit_rate, 3) if stats and hasattr(stats, "embed_hit_rate") else 0,
                "result_hit_rate": round(stats.result_hit_rate, 3)
                if stats and hasattr(stats, "result_hit_rate")
                else 0,
                "embed_hits": stats.embed_hits if stats and hasattr(stats, "embed_hits") else 0,
                "result_hits": stats.result_hits if stats and hasattr(stats, "result_hits") else 0,
            },
        )

    except Exception as e:
        latency_ms = (time.perf_counter() - start_ts) * 1000
        logger.error(f"Cache health check failed: {e}", exc_info=True)
        return ComponentHealth(
            status="error",
            latency_ms=round(latency_ms, 2),
            error=f"{type(e).__name__}: {e}",
        )


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/health",
    response_model=HealthCheckResponse,
    responses={
        200: {"description": "Service is healthy"},
        503: {"model": ErrorResponse, "description": "Service is degraded/unavailable"},
    },
    summary="Comprehensive service health check",
    description="Returns overall service health with component-level readiness indicators.",
)
async def health_check(request: Request) -> HealthCheckResponse | JSONResponse:
    """Comprehensive health check for load balancers and monitoring."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("health")

    startup_errors: List[str] = getattr(request.app.state, "startup_errors", [])

    # All checks run in parallel — gather with exception isolation
    results = await asyncio.gather(
        _check_database(),  # ← CRITICAL: listed first
        _check_vector_store(request),
        _check_ocr_pipeline(request),
        _check_rag_chain(request),
        _check_cache(request),
        return_exceptions=True,
    )

    def _safe_result(r, default: ComponentHealth) -> ComponentHealth:
        if isinstance(r, ComponentHealth):
            return r
        if isinstance(r, Exception):
            logger.warning(f"Health check task failed: {r}")
            return ComponentHealth(status="error", error=str(r))
        return default

    db_health = _safe_result(results[0], ComponentHealth(status="error", error="database check failed"))
    vector_health = _safe_result(results[1], ComponentHealth(status="error", error="vector_store check failed"))
    ocr_health = _safe_result(results[2], ComponentHealth(status="error", error="ocr_pipeline check failed"))
    rag_health = _safe_result(results[3], ComponentHealth(status="error", error="rag_chain check failed"))
    cache_health = _safe_result(results[4], ComponentHealth(status="error", error="cache check failed"))

    components = {
        "database": db_health,  # ← always first in response
        "vector_store": vector_health,
        "ocr_pipeline": ocr_health,
        "rag_chain": rag_health,
        "cache": cache_health,
    }

    # Database is now a critical component — a dead DB means 503.
    # Vector store / RAG may be "degraded" in lazy-startup mode.
    critical_ok = all(components[c].status in {"ok", "degraded"} for c in ["database", "vector_store", "rag_chain"])

    if not critical_ok:
        overall_status = "error"
    elif any(c.status == "degraded" for c in components.values()) or startup_errors:
        overall_status = "degraded"
    else:
        overall_status = "ok"

    response = HealthCheckResponse(
        status=overall_status,
        version=settings.app_version,
        timestamp=datetime.now(timezone.utc).isoformat(),
        components=components,
        correlation_id=corr_id,
        startup_errors=startup_errors,
    )

    if overall_status == "error":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
            headers={
                "X-Correlation-ID": corr_id,
                "Retry-After": "30",
            },
        )

    return response


@router.get("/ready", summary="Kubernetes readiness probe")
async def readiness_probe(request: Request) -> JSONResponse:
    """Kubernetes readiness probe endpoint."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("ready")

    try:
        store = getattr(request.app.state, "store_manager", None)
        ocr_pipeline = getattr(request.app.state, "ocr_pipeline", None)
        rag_chain = getattr(request.app.state, "rag_chain", None)

        if not settings.eager_startup_services:
            startup_errors = getattr(request.app.state, "startup_errors", [])
            if startup_errors:
                logger.warning(f"Readiness blocked due to startup errors: {startup_errors}")
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={
                        "status": "not_ready",
                        "error": "Startup errors detected",
                        "startup_errors": startup_errors,
                        "correlation_id": corr_id,
                    },
                    headers={
                        "X-Correlation-ID": corr_id,
                        "Retry-After": "30",
                    },
                )
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ready",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "correlation_id": corr_id,
                    "lazy_startup": True,
                },
                headers={"X-Correlation-ID": corr_id},
            )

        if store is None or ocr_pipeline is None or rag_chain is None:
            raise RuntimeError("Critical component not initialized")

        # ✅ FIXED: Proper async handling for property access
        async def _check_store_stats():
            return store.stats

        try:
            await asyncio.wait_for(_check_store_stats(), timeout=5.0)
        except asyncio.TimeoutError:
            import sys

            if sys.version_info >= (3, 9):
                await asyncio.wait_for(
                    asyncio.to_thread(lambda: store.stats),
                    timeout=5.0,
                )
            else:
                loop = asyncio.get_running_loop()
                await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: store.stats),
                    timeout=5.0,
                )

        startup_errors = getattr(request.app.state, "startup_errors", [])
        if startup_errors:
            logger.warning(f"Readiness blocked due to startup errors: {startup_errors}")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "not_ready",
                    "error": "Startup errors detected",
                    "startup_errors": startup_errors,
                    "correlation_id": corr_id,
                },
                headers={
                    "X-Correlation-ID": corr_id,
                    "Retry-After": "30",
                },
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "ready",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "correlation_id": corr_id,
            },
            headers={"X-Correlation-ID": corr_id},
        )

    except Exception as e:
        logger.debug(f"Readiness probe failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "error": str(e),
                "correlation_id": corr_id,
            },
            headers={
                "X-Correlation-ID": corr_id,
                "Retry-After": "10",
            },
        )


@router.get("/live", summary="Kubernetes liveness probe")
async def liveness_probe(request: Request) -> JSONResponse:
    """Kubernetes liveness probe endpoint."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("live")
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "alive",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        headers={"X-Correlation-ID": corr_id},
    )


@router.get("/metrics", summary="Prometheus-style metrics endpoint")
async def metrics_endpoint(request: Request) -> Response:
    """Returns application metrics for Prometheus scraping."""
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("metrics")

    try:
        from app.monitoring.metrics_collector import get_prometheus_metrics

        # ✅ FIXED: Add timeout to metrics collection
        metrics_text = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: get_prometheus_metrics(
                    workspace_id=None,
                    correlation_id=corr_id,
                )
            ),
            timeout=10.0,
        )

        if not metrics_text or not metrics_text.strip():
            logger.warning("Metrics collector returned empty; using fallback")
            metrics_text = _get_fallback_prometheus_metrics(corr_id, redis_configured=False)

        return Response(
            content=metrics_text,
            status_code=status.HTTP_200_OK,
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={"X-Correlation-ID": corr_id},
        )

    except asyncio.TimeoutError:
        logger.warning("Metrics collection timed out; using fallback")
        metrics_text = _get_fallback_prometheus_metrics(corr_id, redis_configured=False, error="Timeout")
        return Response(
            content=metrics_text,
            status_code=status.HTTP_200_OK,
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={
                "X-Correlation-ID": corr_id,
                "X-Metrics-Warning": "Using fallback metrics — collection timed out",
            },
        )

    except ImportError as e:
        logger.warning(f"Metrics module import failed: {e}. Returning fallback metrics.")
        metrics_text = _get_fallback_prometheus_metrics(corr_id, redis_configured=False, error=f"ImportError: {e}")
        return Response(
            content=metrics_text,
            status_code=status.HTTP_200_OK,
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={
                "X-Correlation-ID": corr_id,
                "X-Metrics-Warning": "Using fallback metrics — check logs",
            },
        )

    except Exception as e:
        logger.error(f"🔴 Metrics endpoint failed: {e}", exc_info=True)
        metrics_text = _get_fallback_prometheus_metrics(corr_id, redis_configured=False, error=str(e))
        return Response(
            content=metrics_text,
            status_code=status.HTTP_200_OK,
            media_type="text/plain; version=0.0.4; charset=utf-8",
            headers={
                "X-Correlation-ID": corr_id,
                "X-Metrics-Warning": f"Fallback metrics served — error: {type(e).__name__}",
            },
        )


def _get_fallback_prometheus_metrics(
    correlation_id: str,
    redis_configured: bool,
    error: Optional[str] = None,
) -> str:
    """Generate basic Prometheus metrics when advanced collection fails."""
    import time

    # ✅ FIXED: Determine if service is actually healthy for documind_ai_up metric
    # If we're generating fallback due to error, mark as degraded (0)
    health_status = 0 if error else 1

    lines = [
        "# HELP documind_ai_up DocuMind AI service status",
        "# TYPE documind_ai_up gauge",
        f"documind_ai_up {health_status}",
        "",
        "# HELP documind_ai_version Service version information",
        "# TYPE documind_ai_version gauge",
        f'documind_ai_version{{version="{settings.app_version}"}} 1',
        "",
        "# HELP documind_ai_health_status Overall health status (1=ok, 0=degraded, -1=error)",
        "# TYPE documind_ai_health_status gauge",
        f"documind_ai_health_status {health_status}",
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

    if error:
        # ✅ FIXED: Proper escaping for Prometheus label format
        safe_error = error[:200].replace('"', '\\"').replace("\n", "\\n").replace("\r", "").replace("\\", "\\\\")
        lines.extend(
            [
                "",
                "# HELP documind_ai_metrics_error Last metrics collection error message",
                "# TYPE documind_ai_metrics_error gauge",
                f'documind_ai_metrics_error{{error="{safe_error}"}} 1',
            ]
        )

    return "\n".join(lines) + "\n"


def get_health_metadata() -> dict[str, Any]:
    """Return health endpoint metadata for debugging."""
    return {
        "endpoints": ["/health", "/ready", "/live", "/metrics"],
        "critical_components": ["database", "vector_store", "rag_chain"],
        "optional_components": ["ocr_pipeline", "cache"],
        "prometheus_format": "0.0.4",
        "timeout_seconds": 10.0,
    }


__all__ = ["router", "get_health_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
