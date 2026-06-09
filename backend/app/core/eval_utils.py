# backend/app/core/eval_utils.py
# DVMELTSS-FIX: M - Modular, E - Error handling, A - Async
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - Async-safe LLM calls
"""
Shared utilities for evaluation modules (RAGAS, OCR, retrieval).

Centralizes:
- Async-safe LLM calls with retry logic
- Embedding cache management
- Metric aggregation helpers
- Correlation ID propagation

Usage:
    from app.core.eval_utils import call_llm_with_retry, aggregate_metrics
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final, Optional, TypeVar

# ✅ FIXED: Added missing import for generate_correlation_id
from app.core.ids import generate_correlation_id
from app.core.llm_pool import get_llm
from app.core.retry import retry_async, RetryConfig
from app.core.serializers import safe_json_loads

logger = logging.getLogger(__name__)

# DVMELTSS-S: Default retry config for evaluation LLM calls
_EVAL_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    backoff_max=5.0,
    exceptions=(Exception,),
)

# DVMELTSS-S: Default timeout for evaluation calls
_EVAL_TIMEOUT_SECONDS: Final = 60.0

T = TypeVar("T")


async def call_llm_with_retry(
    prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 1000,
    temperature: float = 0.0,
    response_format: Optional[dict] = None,
    extract_key: Optional[str] = None,
    default_value: Any = None,
    correlation_id: Optional[str] = None,
) -> Any:
    """
    Call LLM with retry logic, async-safe, and JSON parsing.

    Args:
        prompt: Prompt to send to LLM
        model: Optional model override
        max_tokens: Max response tokens
        temperature: Sampling temperature (0.0 for deterministic)
        response_format: Optional {"type": "json_object"} for structured output
        extract_key: If set, extract this key from JSON response
        default_value: Value to return on failure
        correlation_id: Request ID for tracing

    Returns:
        Parsed response or default_value on failure
    """
    corr_id = correlation_id or "eval_unknown"

    # Get LLM from centralized pool
    llm = get_llm(
        streaming=False,
        model_override=model,
        temperature_override=temperature,
    )

    @retry_async(config=_EVAL_RETRY_CONFIG)
    async def _do_call():
        # Use async-safe thread execution for blocking OpenAI calls
        response = await asyncio.to_thread(
            lambda: llm.invoke(
                [{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                response_format=response_format,
            )
        )
        return response.content

    try:
        content = await _do_call()
        if not content:
            return default_value

        # Parse JSON if requested
        if response_format and response_format.get("type") == "json_object":
            data = safe_json_loads(content, default={})
            if extract_key and isinstance(data, dict):
                return data.get(extract_key, default_value)
            return data

        return content

    except Exception as e:
        logger.warning(f"[{corr_id}] LLM call failed: {e}")
        return default_value


def aggregate_metrics(
    values: list[float],
    metric_name: str,
    min_samples: int = 5,
    bootstrap_samples: int = 1000,
) -> dict[str, Any]:
    """
    Aggregate metrics with confidence intervals.

    Args:
        values: List of metric values [0.0, 1.0]
        metric_name: Name for logging
        min_samples: Minimum samples for statistical validity
        bootstrap_samples: Number of bootstrap iterations for CI

    Returns:
        Dict with mean, std, and 95% CI if enough samples
    """
    import numpy as np

    if not values:
        return {
            "mean": 0.0,
            "std": 0.0,
            "n": 0,
            "ci_95_lower": None,
            "ci_95_upper": None,
            "statistically_valid": False,
        }

    arr = np.array(values)
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    n = len(values)

    result = {
        "mean": round(mean, 4),
        "std": round(std, 4),
        "n": n,
        "statistically_valid": n >= min_samples,
    }

    # Compute bootstrap CI if enough samples
    if n >= min_samples and std > 1e-10:
        try:
            means = [np.mean(np.random.choice(arr, size=n, replace=True)) for _ in range(bootstrap_samples)]
            result["ci_95_lower"] = round(float(np.percentile(means, 2.5)), 4)
            result["ci_95_upper"] = round(float(np.percentile(means, 97.5)), 4)
        except Exception as e:
            logger.warning(f"Bootstrap CI failed for {metric_name}: {e}")
            result["ci_95_lower"] = None
            result["ci_95_upper"] = None
    else:
        result["ci_95_lower"] = None
        result["ci_95_upper"] = None

    return result


def generate_eval_correlation_id(prefix: str = "eval") -> str:
    """Generate correlation ID for evaluation tracing."""
    # ✅ FIXED: Now generate_correlation_id is imported and defined
    return f"{prefix}_{generate_correlation_id()}"


def validate_eval_sample(data: dict, required_fields: list[str]) -> tuple[bool, str]:
    """Validate evaluation sample has required fields."""
    if not isinstance(data, dict):
        return False, f"Expected dict, got {type(data).__name__}"

    missing = [f for f in required_fields if f not in data]
    if missing:
        return False, f"Missing required fields: {missing}"

    return True, ""


# DVMELTSS-M: Explicit module exports
__all__ = [
    "call_llm_with_retry",
    "aggregate_metrics",
    "generate_eval_correlation_id",
    "validate_eval_sample",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
