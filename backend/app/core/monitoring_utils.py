"""
Shared utilities for monitoring modules.

Centralizes:
- Async Redis client for metrics storage
- Configurable quality thresholds
- Correlation ID propagation helpers
- Safe statistical computations

Usage:
    from app.core.monitoring_utils import get_async_redis, compute_percentile
"""

from __future__ import annotations

import logging
from typing import Final, Optional

from redis.asyncio import from_url
import numpy as np

from app.config import get_settings

from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: Default monitoring thresholds (configurable via settings)
_DEFAULT_QUALITY_THRESHOLDS: Final = {
    "confidence_score": 0.65,
    "relevance_score": 0.60,
    "faithfulness": 0.70,
    "web_search_rate": 0.40,
    "human_review_rate": 0.20,
    "latency_ms_p95": 8000,
}

# DVMELTSS-S: Redis connection defaults for monitoring
_MONITORING_REDIS_DB: Final = 3
_REDIS_TIMEOUT: Final = 5.0


async def get_monitoring_redis(
    redis_url: Optional[str] = None,
    db: int = _MONITORING_REDIS_DB,
):
    """
    Get async Redis client configured for monitoring operations.

    Args:
        redis_url: Optional override for Redis URL
        db: Redis database number (default: 3 for monitoring)

    Returns:
        Configured Redis async instance
    """
    settings = get_settings()
    url = redis_url or getattr(settings, "redis_url", f"redis://localhost:6379/{db}")

    return await from_url(
        url,
        db=db,
        decode_responses=True,
        socket_connect_timeout=_REDIS_TIMEOUT,
        socket_timeout=_REDIS_TIMEOUT,
        health_check_interval=30,
        retry_on_timeout=True,
        max_connections=10,  # Monitoring is read-heavy
    )


def get_quality_thresholds(override: Optional[dict] = None) -> dict:
    """
    Get quality alert thresholds with optional override.

    Args:
        override: Optional dict to override default thresholds

    Returns:
        Dict of metric_name -> threshold_value
    """
    settings = get_settings()
    # Allow env-based overrides
    config_thresholds = getattr(settings, "monitoring_quality_thresholds", {})

    base = _DEFAULT_QUALITY_THRESHOLDS.copy()
    base.update(config_thresholds)
    if override:
        base.update(override)

    return base


def compute_percentile(values: list[float], percentile: float) -> Optional[float]:
    """
    Compute percentile safely with NaN handling.

    Args:
        values: List of numeric values
        percentile: Percentile to compute (0-100)

    Returns:
        Percentile value or None if insufficient data
    """
    if not values or len(values) < 2:
        return None

    clean_values = [v for v in values if v is not None and not np.isnan(v)]
    if len(clean_values) < 2:
        return None

    return float(np.percentile(clean_values, percentile))


def compute_mean(values: list[Optional[float]]) -> Optional[float]:
    """
    Compute mean safely with None/NaN filtering.

    Args:
        values: List of optional numeric values

    Returns:
        Mean value or None if insufficient valid data
    """
    if not values:
        return None

    clean_values = [v for v in values if v is not None and not np.isnan(v)]
    if not clean_values:
        return None

    return float(np.mean(clean_values))


def validate_monitoring_window(hours: float, min_hours: float = 1.0, max_hours: float = 720.0) -> float:
    """
    Validate and clamp monitoring window hours.

    Args:
        hours: Requested window in hours
        min_hours: Minimum allowed window
        max_hours: Maximum allowed window

    Returns:
        Clamped window value
    """
    if hours < min_hours:
        logger.warning(f"Monitoring window {hours}h < min {min_hours}h — clamping")
        return min_hours
    if hours > max_hours:
        logger.warning(f"Monitoring window {hours}h > max {max_hours}h — clamping")
        return max_hours
    return hours


def generate_monitoring_correlation_id(prefix: str = "monitor") -> str:
    """Generate correlation ID for monitoring operations."""
    return f"{prefix}_{generate_correlation_id()}"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "get_monitoring_redis",
    "get_quality_thresholds",
    "compute_percentile",
    "compute_mean",
    "validate_monitoring_window",
    "generate_monitoring_correlation_id",
]
# Local smoke test entry point. Run: python -m

