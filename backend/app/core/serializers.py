"""
Shared serialization utilities for DocuMind AI.

Centralizes JSON handling with safe defaults, graceful degradation,
and consistent error logging across all modules.

Usage:
    from app.core.serializers import safe_json_dumps, safe_json_loads
"""

from __future__ import annotations

import json
import logging
from typing import Any, Final, Optional, TypeVar

logger = logging.getLogger(__name__)

# DVMELTSS-S: Immutable JSON config — safe, compact defaults
_JSON_DUMP_CONFIG: Final = {
    "ensure_ascii": False,  # Support Unicode
    "separators": (",", ":"),  # Compact output
    "default": str,  # Fallback for non-serializable (use with caution)
}

_T = TypeVar("_T")


def safe_json_dumps(obj: Any, **overrides: Any) -> str:
    """
    Serialize to JSON with safe defaults + override support.

    Args:
        obj: Object to serialize
        **overrides: Optional config overrides (e.g., indent=2 for debugging)

    Returns:
        JSON string

    Raises:
        TypeError: If object cannot be serialized even with fallback
    """
    config = _JSON_DUMP_CONFIG | overrides
    try:
        return json.dumps(obj, **config)
    except (TypeError, ValueError) as e:
        logger.error(f"JSON serialization failed for {type(obj).__name__}: {e}")
        # DVMELTSS-E: Re-raise to let caller decide fallback strategy
        raise


def safe_json_loads(raw: str, default: Optional[_T] = None) -> _T | dict | list | None:
    """
    Deserialize JSON with graceful fallback.

    Args:
        raw: JSON string to parse
        default: Value to return on parse failure (default: None)

    Returns:
        Parsed object, or default on failure
    """
    if not raw or not isinstance(raw, str):
        return default

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed at pos {e.pos}: {e.msg}. Raw preview: {raw[:150]}...")
        return default


def safe_json_loads_strict(raw: str) -> dict | list:
    """
    Deserialize JSON with strict error handling — raises on failure.
    Use when parse failure is a critical error.

    Raises:
        json.JSONDecodeError: If parsing fails
    """
    if not raw or not isinstance(raw, str):
        raise json.JSONDecodeError("Empty or invalid input", "", 0)
    return json.loads(raw)


def cache_serialize(obj: Any) -> str:
    """
    Serialize object for Redis cache storage.
    Adds metadata for cache validation.
    """
    return safe_json_dumps(
        {
            "_cache_version": "1.0",
            "_serialized_at": __import__("time").time(),
            "data": obj,
        }
    )


def cache_deserialize(raw: str) -> Optional[dict]:
    """
    Deserialize object from Redis cache with version check.
    Returns None if cache format is incompatible.
    """
    data = safe_json_loads(raw)
    if not isinstance(data, dict):
        return None
    if data.get("_cache_version") != "1.0":
        logger.warning(f"Cache format mismatch: version={data.get('_cache_version')}")
        return None
    return data.get("data")


# DVMELTSS-M: Explicit module exports
__all__ = [
    "safe_json_dumps",
    "safe_json_loads",
    "safe_json_loads_strict",
    "cache_serialize",
    "cache_deserialize",
]
# Local smoke test entry point. Run: python -m

