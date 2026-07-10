
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, Optional, TypeVar, Final

# DVMELTSS-M: Import centralized utilities
from app.core.retry import RetryConfig

logger = logging.getLogger(__name__)

# Type variable for generic return type
T = TypeVar("T")

# DVMELTSS-E: Retry config for LangSmith API calls
_LANGSMITH_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    backoff_max=5.0,
    exceptions=(Exception,),
)

_LANGSMITH_TIMEOUT: Final = 30.0

# Check LangSmith availability once at module load.
# the `@traceable` decorator API. We try the new decorator API first, then fall back to
# the legacy context manager API for older installs.  All paths degrade gracefully.
#
# Preferred (langsmith >= 0.1.48): langsmith.traceable
# Legacy  (langsmith <  0.1.48):   langsmith.trace / langsmith.atrace

_ls_traceable = None  # new decorator-based API
_ls_trace = None  # legacy sync context manager
_ls_atrace = None  # legacy async context manager
_LANGSMITH_SYNC_AVAILABLE = False
_LANGSMITH_ASYNC_AVAILABLE = False

try:
    from langsmith import traceable as _ls_traceable  # preferred API

    _LANGSMITH_SYNC_AVAILABLE = True
    _LANGSMITH_ASYNC_AVAILABLE = True  # traceable handles both sync and async
    logger.debug("langsmith.traceable available — using decorator API")
except ImportError:
    # Fall back to legacy context manager API (langsmith < 0.1.48)
    try:
        from langsmith import trace as _ls_trace  # type: ignore[assignment]

        _LANGSMITH_SYNC_AVAILABLE = True
        logger.debug("langsmith.trace available — using legacy context manager API")
    except ImportError:
        logger.debug("langsmith.trace not available — sync tracing disabled")

    try:
        from langsmith import atrace as _ls_atrace  # type: ignore[assignment]

        _LANGSMITH_ASYNC_AVAILABLE = True
        logger.debug("langsmith.atrace available — async tracing via legacy API")
    except ImportError:
        logger.debug("langsmith.atrace not available — async tracing disabled")


def _validate_tracer_inputs(
    name: Optional[str],
    run_type: str,
    tags: Optional[list],
    metadata: Optional[dict],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate tracer decorator inputs before applying."""
    if name is not None and not isinstance(name, str):
        return False, "name must be a string or None"
    if not isinstance(run_type, str):
        return False, "run_type must be a string"
    if tags is not None and not isinstance(tags, list):
        return False, "tags must be a list or None"
    if metadata is not None and not isinstance(metadata, dict):
        return False, "metadata must be a dict or None"
    return True, ""


def traceable(
    name: Optional[str] = None,
    run_type: str = "chain",
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
    project_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
):
    """
    Decorator to add LangSmith tracing to any Python function.

    Features:
    - Works with both sync and async functions
    - Gracefully degrades when LangSmith unavailable
    - Logs execution time at DEBUG level (lazy evaluation)
    - Preserves function signature and docstring
    - FIXED: Accepts correlation_id for distributed tracing

    Args:
        name: Optional span name (defaults to function qualname)
        run_type: LangSmith run type ("chain", "tool", "llm", etc.)
        tags: Optional list of tags for filtering in UI
        metadata: Optional dict of custom metadata
        project_name: Optional override for LangSmith project
        correlation_id: Request ID for distributed tracing

    Returns:
        Decorated function that traces execution when LangSmith available
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        # ✅ Validate inputs at decorator application time
        is_valid, error = _validate_tracer_inputs(name, run_type, tags, metadata, correlation_id or "tracer_init")
        if not is_valid:
            logger.warning(f"Invalid tracer inputs for {fn.__qualname__}: {error}")
            # Return original function without tracing
            return fn

        span_name = name or fn.__qualname__
        span_tags = tags or []
        span_metadata = {
            **(metadata or {}),
            **({"correlation_id": correlation_id} if correlation_id else {}),
        }

        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                return await _run_with_trace_async(
                    fn,
                    args,
                    kwargs,
                    span_name,
                    run_type,
                    span_tags,
                    span_metadata,
                    project_name,
                    correlation_id,
                )

            return async_wrapper
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                return _run_with_trace_sync(
                    fn,
                    args,
                    kwargs,
                    span_name,
                    run_type,
                    span_tags,
                    span_metadata,
                    project_name,
                    correlation_id,
                )

            return sync_wrapper

    return decorator


def _run_with_trace_sync(
    fn: Callable[..., T],
    args: tuple,
    kwargs: dict,
    name: str,
    run_type: str,
    tags: list[str],
    metadata: dict[str, Any],
    project_name: Optional[str],
    correlation_id: Optional[str],
) -> T:
    """
    Execute sync function with LangSmith tracing wrapper.

    Falls back to direct execution if tracing unavailable or fails.
    ✅ FIXED: Proper sync retry + resource cleanup.
    """
    corr_id = correlation_id or "trace_sync"

    # Fast path: skip all overhead if tracing not available
    if not _LANGSMITH_SYNC_AVAILABLE:
        return fn(*args, **kwargs)

    try:
        start = time.perf_counter()

        if _ls_traceable is not None:
            traced_fn = _ls_traceable(
                run_type=run_type,
                name=name,
                tags=tags,
                metadata=metadata,
                project_name=project_name,
            )(fn)
            result = traced_fn(*args, **kwargs)
        elif _ls_trace is not None:
            # Legacy: sync context manager with retry loop
            last_error = None
            for attempt in range(_LANGSMITH_RETRY_CONFIG.max_attempts):
                try:
                    with _ls_trace(
                        name=name,
                        run_type=run_type,
                        tags=tags,
                        metadata=metadata,
                        project_name=project_name,
                    ):
                        result = fn(*args, **kwargs)
                        break
                except Exception as e:
                    last_error = e
                    if attempt < _LANGSMITH_RETRY_CONFIG.max_attempts - 1:
                        wait = min(
                            _LANGSMITH_RETRY_CONFIG.backoff_base * (2**attempt),
                            _LANGSMITH_RETRY_CONFIG.backoff_max,
                        )
                        time.sleep(wait)
                    else:
                        raise
            else:
                logger.warning(f"[Trace] {name} span exhausted retries: {last_error}. | {corr_id}")
                return fn(*args, **kwargs)
        else:
            return fn(*args, **kwargs)

        elapsed_ms = (time.perf_counter() - start) * 1000
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("[Trace] %s: %.0fms | %s", name, elapsed_ms, corr_id)
        return result

    except Exception as e:
        # Log warning but don't fail the function — tracing is observability, not correctness
        logger.warning(f"[Trace] {name} span failed: {e}. Running without trace. | {corr_id}")
        return fn(*args, **kwargs)


async def _run_with_trace_async(
    fn: Callable[..., T],
    args: tuple,
    kwargs: dict,
    name: str,
    run_type: str,
    tags: list[str],
    metadata: dict[str, Any],
    project_name: Optional[str],
    correlation_id: Optional[str],
) -> T:
    """
    Execute async function with LangSmith async tracing wrapper.

    Uses async context manager to avoid blocking event loop.
    ✅ FIXED: Proper async context manager usage + timeout.
    """
    corr_id = correlation_id or "trace_async"

    # Fast path: skip all overhead if tracing not available
    if not _LANGSMITH_ASYNC_AVAILABLE:
        return await fn(*args, **kwargs)

    try:
        start = time.perf_counter()

        if _ls_traceable is not None:
            traced_fn = _ls_traceable(
                run_type=run_type,
                name=name,
                tags=tags,
                metadata=metadata,
                project_name=project_name,
            )(fn)
            result = await asyncio.wait_for(
                traced_fn(*args, **kwargs),
                timeout=_LANGSMITH_TIMEOUT,
            )
        elif _ls_atrace is not None:
            # Legacy: async context manager (no await on the context manager itself)
            async with _ls_atrace(
                name=name,
                run_type=run_type,
                tags=tags,
                metadata=metadata,
                project_name=project_name,
            ):
                result = await asyncio.wait_for(
                    fn(*args, **kwargs),
                    timeout=_LANGSMITH_TIMEOUT,
                )
        else:
            return await fn(*args, **kwargs)

        elapsed_ms = (time.perf_counter() - start) * 1000
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("[Trace] async %s: %.0fms | %s", name, elapsed_ms, corr_id)
        return result

    except asyncio.TimeoutError:
        logger.warning(
            f"[Trace] async {name} timed out after {_LANGSMITH_TIMEOUT}s. Running without trace. | {corr_id}"
        )
        return await fn(*args, **kwargs)
    except Exception as e:
        logger.warning(f"[Trace] async {name} span failed: {e}. Running without trace. | {corr_id}")
        return await fn(*args, **kwargs)


# Convenience decorators for common use cases
def trace_chain(name: Optional[str] = None, **kwargs):
    """Decorator for RAG chain steps (run_type='chain')."""
    return traceable(name=name, run_type="chain", **kwargs)


def trace_tool(name: Optional[str] = None, **kwargs):
    """Decorator for tool/function calls (run_type='tool')."""
    return traceable(name=name, run_type="tool", **kwargs)


def trace_llm(name: Optional[str] = None, **kwargs):
    """Decorator for LLM calls (run_type='llm')."""
    return traceable(name=name, run_type="llm", **kwargs)


def get_tracer_metadata() -> dict[str, Any]:
    """✅ NEW: Return tracer metadata for debugging."""
    return {
        "sync_available": _LANGSMITH_SYNC_AVAILABLE,
        "async_available": _LANGSMITH_ASYNC_AVAILABLE,
        "retry_config": {
            "max_attempts": _LANGSMITH_RETRY_CONFIG.max_attempts,
            "backoff_base": _LANGSMITH_RETRY_CONFIG.backoff_base,
            "backoff_max": _LANGSMITH_RETRY_CONFIG.backoff_max,
        },
        "timeout_seconds": _LANGSMITH_TIMEOUT,
        "supported_run_types": ["chain", "tool", "llm", "retriever", "embedding"],
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "traceable",
    "trace_chain",
    "trace_tool",
    "trace_llm",
    "get_tracer_metadata",
]
# Local smoke test entry point. Run: python -m

