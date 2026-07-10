
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Final, Optional, Any

# DVMELTSS-M: Import centralized config
from app.config import get_settings


@dataclass(frozen=True)
class QueueConfig:
    """
    Configuration for a task in the queue.
    ✅ FIXED: Frozen for immutability + field validation.
    """

    queue: str  # Celery queue name
    priority: int = field(default=5)  # 1 (low) to 10 (high)
    countdown: int = field(default=0)  # delay before processing (seconds)
    expires: int = field(default=7200)  # task expiry (seconds)

    def __post_init__(self):
        # ✅ Validate priority range (Celery expects 1-10)
        if not (1 <= self.priority <= 10):
            raise ValueError(f"priority must be between 1 and 10, got {self.priority}")
        # ✅ Validate countdown is non-negative
        if self.countdown < 0:
            raise ValueError(f"countdown must be non-negative, got {self.countdown}")
        # ✅ Validate expires is positive
        if self.expires <= 0:
            raise ValueError(f"expires must be positive, got {self.expires}")

    def with_overrides(self, **overrides: Any) -> "QueueConfig":
        """Create a new config with specified fields overridden."""
        return replace(self, **overrides)


_TIER_CONFIGS: Final = MappingProxyType(
    {
        "high": QueueConfig(queue="high_priority", priority=9),
        "default": QueueConfig(queue="default", priority=5),
        "bulk": QueueConfig(queue="bulk", priority=1, countdown=2),
    }
)


def _validate_workspace_id(workspace_id: str, corr_id: str = "priority") -> tuple[bool, str]:
    """Validate workspace_id format."""
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return False, "workspace_id must be a non-empty string"
    # Allow alphanumeric, underscore, hyphen
    if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", workspace_id):
        return (
            False,
            "workspace_id may contain only letters, numbers, underscores, and hyphens (max 64 chars)",
        )
    return True, ""


# Configurable thresholds
def _get_file_size_thresholds() -> tuple[float, float]:
    """Get file size thresholds from settings."""
    settings = get_settings()
    return (
        getattr(settings, "task_large_file_threshold_mb", 50.0),
        getattr(settings, "task_bulk_file_threshold_mb", 200.0),
    )


def get_queue_config(
    workspace_id: str,
    explicit_tier: Optional[str] = None,
    file_size_mb: float = 0.0,
) -> QueueConfig:
    """
    Determine queue configuration for an ingestion task.

    Priority logic:
    1. Explicit tier override (from request parameter)
    2. File size heuristic: > threshold -> bulk queue
    3. Default: standard queue

    In production: replace with database workspace tier lookup.

    ✅ FIXED: Input validation + safe instance returns.
    """
    corr_id = "priority_unknown"

    # ✅ Validate workspace_id
    is_valid, error = _validate_workspace_id(workspace_id, corr_id)
    if not is_valid:
        # Log warning but fall back to default config
        from app.core.logging_config import get_logger

        logger = get_logger(__name__)
        logger.warning(f"[{corr_id}] Invalid workspace_id: {error} — using default queue config")

    # ✅ Clamp file_size_mb to non-negative
    file_size_mb = max(0.0, file_size_mb)

    # DVMELTSS-V: Validate explicit_tier
    if explicit_tier and explicit_tier in _TIER_CONFIGS:
        # ✅ Return a copy to prevent mutation of shared config
        return replace(_TIER_CONFIGS[explicit_tier])

    # Large file -> bulk queue to avoid blocking workers
    large_threshold, bulk_threshold = _get_file_size_thresholds()
    if file_size_mb > bulk_threshold:
        return replace(_TIER_CONFIGS["bulk"])
    elif file_size_mb > large_threshold:
        return replace(_TIER_CONFIGS["high"])

    return replace(_TIER_CONFIGS["default"])


def get_priority_metadata() -> dict[str, Any]:
    """✅ NEW: Return priority metadata for monitoring."""
    return {
        "tier_configs": {
            tier: {
                "queue": config.queue,
                "priority": config.priority,
                "countdown": config.countdown,
                "expires": config.expires,
            }
            for tier, config in _TIER_CONFIGS.items()
        },
        "file_size_thresholds": {
            "large_mb": _get_file_size_thresholds()[0],
            "bulk_mb": _get_file_size_thresholds()[1],
        },
        "priority_range": {"min": 1, "max": 10},
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["QueueConfig", "get_queue_config", "get_priority_metadata"]
# Local smoke test entry point. Run: python -m

