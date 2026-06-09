# backend/app/core/celery_utils.py
# DVMELTSS-FIX: M - Modular, A - Async-safe, E - Error handling
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - True async for task orchestration
"""
Shared Celery utilities for DocuMind AI.

Centralizes:
- Async-safe event loop handling
- Correlation ID propagation for distributed tracing
- Retry logic with exponential backoff
- Queue configuration helpers

Usage:
    from app.core.celery_utils import get_running_loop_safe, propagate_correlation_id
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
from typing import Any, Callable, Final, Optional


logger = logging.getLogger(__name__)

# DVMELTSS-S: Default retry configuration
_CELERY_RETRY_MAX_ATTEMPTS: Final = 3
_CELERY_RETRY_BASE_DELAY: Final = 1.0
_CELERY_RETRY_MAX_DELAY: Final = 30.0


def get_running_loop_safe() -> Optional[asyncio.AbstractEventLoop]:
    """
    Get the current running event loop safely.
    Returns None if no loop is running (sync context).
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def run_async_in_task(
    func: Callable[..., Any],
    *args,
    timeout: Optional[float] = None,
    **kwargs,
) -> Any:
    """
    Run an async callable from synchronous task/adapter code.

    # FIXED: This helper is intentionally synchronous because callers use it
    as a sync bridge. Returning a coroutine here leaks into API responses and
    background tasks.
    # OPTIMIZED: If a loop is already running, execute the coroutine on a
    short-lived worker thread with its own event loop to avoid nested-loop
    runtime errors.
    """

    async def _run() -> Any:
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _run_with_timeout() -> Any:
        if timeout is None:
            return await _run()
        return await asyncio.wait_for(_run(), timeout=timeout)

    loop = get_running_loop_safe()
    if loop and not loop.is_closed():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(lambda: asyncio.run(_run_with_timeout()))
            return future.result(timeout=timeout + 1 if timeout is not None else None)

    return asyncio.run(_run_with_timeout())


def propagate_correlation_id(
    correlation_id: Optional[str],
    extra_context: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Build logging context with correlation ID for distributed tracing.
    """
    context = {"correlation_id": correlation_id or "unknown"}
    if extra_context:
        context.update(extra_context)
    return context


def is_transient_error(exc: Exception) -> bool:
    """
    Identify errors worth retrying (network, rate limit, temp issues).
    """
    transient_patterns = [
        "rate limit",
        "timeout",
        "connection",
        "temporary",
        "503",
        "502",
        "429",
        "redis",
        "broker",
    ]
    msg = str(exc).lower()
    return any(p in msg for p in transient_patterns)


def get_queue_for_file_size(
    file_size_mb: float,
    high_threshold: float = 50.0,
    bulk_threshold: float = 200.0,
) -> str:
    """
    Determine Celery queue based on file size.

    Args:
        file_size_mb: File size in megabytes
        high_threshold: Files > this go to high_priority
        bulk_threshold: Files > this go to bulk queue

    Returns:
        Queue name: "high_priority", "default", or "bulk"
    """
    if file_size_mb > bulk_threshold:
        return "bulk"
    elif file_size_mb > high_threshold:
        return "high_priority"
    return "default"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "get_running_loop_safe",
    "run_async_in_task",
    "propagate_correlation_id",
    "is_transient_error",
    "get_queue_for_file_size",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
