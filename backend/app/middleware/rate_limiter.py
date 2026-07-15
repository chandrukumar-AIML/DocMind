
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Final, Optional, Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from fastapi import Request, HTTPException, status, Depends

# DVMELTSS-M: Import centralized Redis utilities
from app.core.redis_utils import (
    get_async_redis,
    sanitize_redis_key,
    load_lua_script,
    safe_evalsha,
)
from app.core.celery_utils import run_async_in_task  # ✅ NEW: For safe async execution
from app.config import get_settings

logger = logging.getLogger(__name__)


def _make_counter(name: str, description: str):
    """Return a prometheus_client Counter, or a no-op stub if the package is absent."""
    try:
        from prometheus_client import Counter  # noqa: PLC0415

        return Counter(name, description)
    except Exception:
        class _Noop:
            def inc(self, *a, **kw):
                pass
        return _Noop()


# Fires every time rate limiting is bypassed because Redis is unreachable.
# Alert: monitoring/prometheus/alerts.yml → RateLimiterFailOpen
_FAIL_OPEN_COUNTER = _make_counter(
    "rate_limiter_fail_open_total",
    "Number of times rate limiting was bypassed due to Redis unavailability",
)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-T) -------------------------
# ========================================================================

# Default rate limit configurations per endpoint group
_DEFAULT_RATE_LIMITS: Final = {
    "query": {"requests": 100, "window_seconds": 3600},  # 100/hour
    "ingest": {"requests": 10, "window_seconds": 3600},  # 10/hour (expensive)
    "default": {"requests": 200, "window_seconds": 3600},  # 200/hour
    "domains": {"requests": 50, "window_seconds": 3600},  # 50/hour
}

# Redis key prefix for rate limiting
_REDIS_KEY_PREFIX: Final = "rate"

# DVMELTSS-E: Fail-safe behavior.
# In production the default is fail-CLOSED to prevent Redis outages from disabling
# all rate limiting. Override with RATE_LIMITER_FAIL_OPEN=true only in dev/test.
def _default_fail_open() -> bool:
    settings = get_settings()
    env = getattr(settings, "environment", "dev")
    if env not in ("dev", "test", "development", "testing"):
        return False  # production: fail closed
    return True  # dev/test: fail open to avoid blocking local work


_FAIL_OPEN: bool = _default_fail_open()

_REDIS_TIMEOUT: Final = 10.0

# BATMAN-T: Lua script for atomic sliding window rate limiting
# This script runs atomically in Redis, preventing race conditions
_SLIDING_WINDOW_SCRIPT: Final = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local request_id = ARGV[4]

-- Remove entries older than the window
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)

-- Add current request with unique ID
redis.call('ZADD', key, now, request_id)

-- Count requests in window
local count = redis.call('ZCARD', key)

-- Set TTL to clean up old keys automatically
redis.call('EXPIRE', key, window + 60)  -- Extra 60s buffer for clock skew

-- Return: allowed (1/0), count, limit, reset_time
local reset_at = now + window
return {count <= limit and 1 or 0, count, limit, reset_at}
"""


@dataclass(frozen=True)
class RateLimitResult:
    """
    Immutable result of a rate limit check.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_at: float  # unix timestamp when window resets
    retry_after: int  # seconds until next allowed request
    correlation_id: Optional[str] = None

    def to_headers(self) -> dict:
        """Convert to HTTP headers for response."""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
            "Retry-After": str(self.retry_after) if not self.allowed else "0",
        }


def _validate_rate_limit_inputs(
    workspace_id: Optional[str],
    endpoint_group: Optional[str],
    identifier: Optional[str],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate rate limiter inputs before processing."""
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return False, "workspace_id must be a non-empty string"
    if endpoint_group is not None and not isinstance(endpoint_group, str):
        return False, "endpoint_group must be a string or None"
    if identifier is not None and not isinstance(identifier, str):
        return False, "identifier must be a string or None"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


class RateLimiter:
    """
    Async Redis sliding window rate limiter.

    Algorithm (atomic via Lua script):
    1. Key = rate:{identifier}:{endpoint_group}
    2. Remove members older than window_start
    3. Add current request with unique ID + timestamp
    4. Count members — if > limit: reject
    5. Set TTL = window_seconds for auto-cleanup

    Features (DVMELTSS-V, BATMAN-A, OWASP-3):
    - Async Redis via centralized app.core.redis_utils
    - Atomic Lua script prevents race conditions
    - Unique request IDs prevent duplicate counting
    - Workspace-level isolation via identifier
    - Fail-safe behavior configurable via _FAIL_OPEN
    - Correlation ID tracing for distributed debugging
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        fail_open: bool = _FAIL_OPEN,
    ):
        settings = get_settings()
        self.redis_url = redis_url or getattr(settings, "redis_url", "redis://localhost:6379/2")
        self.fail_open = fail_open
        self._redis: Optional[redis.Redis] = None
        self._script_sha: Optional[str] = None
        logger.info(f"RateLimiter initialized: redis={self.redis_url}, fail_open={fail_open}")

    async def _get_redis(self) -> redis.Redis:
        """Lazy-load async Redis connection with centralized utility."""
        if self._redis is None:
            try:
                self._redis = await asyncio.wait_for(
                    get_async_redis(self.redis_url, db=2),
                    timeout=_REDIS_TIMEOUT,
                )
                self._script_sha = await load_lua_script(self._redis, _SLIDING_WINDOW_SCRIPT)
                logger.debug("Redis connection established + script loaded")
            except asyncio.TimeoutError:
                logger.error(f"Redis connection timed out after {_REDIS_TIMEOUT}s")
                raise
            except Exception as e:
                logger.error(f"Failed to initialize Redis: {e}")
                raise
        return self._redis

    async def close(self):
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            logger.info("Redis connection closed.")

    async def check_async(
        self,
        workspace_id: str,
        endpoint_group: str = "default",
        identifier: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> RateLimitResult:
        """
        Async: Check and record a rate-limited request.
        BATMAN-A: Non-blocking Redis operations via aioredis.
        """
        corr_id = correlation_id or "rate_unknown"

        # ✅ Validate inputs
        is_valid, error = _validate_rate_limit_inputs(workspace_id, endpoint_group, identifier, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid rate limit inputs: {error}")
            # Return safe default on validation failure
            config = _DEFAULT_RATE_LIMITS.get("default", _DEFAULT_RATE_LIMITS["default"])
            now = time.time()
            return RateLimitResult(
                allowed=self.fail_open,
                limit=config["requests"],
                remaining=config["requests"] if self.fail_open else 0,
                reset_at=now + config["window_seconds"],
                retry_after=0 if self.fail_open else config["window_seconds"],
                correlation_id=corr_id,
            )

        config = _DEFAULT_RATE_LIMITS.get(endpoint_group, _DEFAULT_RATE_LIMITS["default"])
        limit = config["requests"]
        window = config["window_seconds"]

        safe_id = sanitize_redis_key(identifier or workspace_id)
        key = sanitize_redis_key(f"{safe_id}:{endpoint_group}", prefix=_REDIS_KEY_PREFIX)

        now = time.time()
        request_id = f"{now}:{uuid.uuid4().hex[:8]}"

        try:
            redis = await self._get_redis()

            result = await asyncio.wait_for(
                safe_evalsha(
                    redis,
                    self._script_sha,
                    keys=[key],
                    args=[str(now), str(window), str(limit), request_id],
                ),
                timeout=_REDIS_TIMEOUT,
            )

            allowed, count, limit_ret, reset_at = result
            allowed = bool(allowed)
            remaining = max(0, int(limit_ret) - int(count))

            if not allowed:
                logger.warning(
                    f"[{corr_id}] Rate limit exceeded: {safe_id} | " f"group={endpoint_group} | count={count}/{limit}"
                )

            return RateLimitResult(
                allowed=allowed,
                limit=int(limit_ret),
                remaining=remaining,
                reset_at=float(reset_at),
                retry_after=int(window) if not allowed else 0,
                correlation_id=corr_id,
            )

        except asyncio.TimeoutError:
            logger.error(f"[{corr_id}] Redis operation timed out after {_REDIS_TIMEOUT}s")
            if self.fail_open:
                _FAIL_OPEN_COUNTER.inc()
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    reset_at=now + window,
                    retry_after=0,
                    correlation_id=corr_id,
                )
            else:
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_at=now + window,
                    retry_after=window,
                    correlation_id=corr_id,
                )
        except RedisError as e:
            logger.warning(f"[{corr_id}] Redis error in rate limiter: {e}")
            if self.fail_open:
                _FAIL_OPEN_COUNTER.inc()
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    reset_at=now + window,
                    retry_after=0,
                    correlation_id=corr_id,
                )
            else:
                # Fail closed — block on Redis failure (stricter)
                return RateLimitResult(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_at=now + window,
                    retry_after=window,
                    correlation_id=corr_id,
                )
        except Exception as e:
            logger.error(f"[{corr_id}] Unexpected rate limiter error: {type(e).__name__}: {e}")
            if self.fail_open:
                return RateLimitResult(
                    allowed=True,
                    limit=limit,
                    remaining=limit,
                    reset_at=now + window,
                    retry_after=0,
                    correlation_id=corr_id,
                )
            else:
                raise

    async def get_usage_async(
        self,
        workspace_id: str,
        endpoint_group: str = "default",
        correlation_id: Optional[str] = None,
    ) -> dict:
        """Async: Get current rate limit usage for a workspace."""
        corr_id = correlation_id or "rate_unknown"

        # ✅ Validate inputs
        is_valid, error = _validate_rate_limit_inputs(workspace_id, endpoint_group, None, correlation_id, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid usage inputs: {error}")
            config = _DEFAULT_RATE_LIMITS.get("default", _DEFAULT_RATE_LIMITS["default"])
            return {
                "limit": config["requests"],
                "used": 0,
                "remaining": config["requests"],
                "window_seconds": config["window_seconds"],
                "reset_in_seconds": config["window_seconds"],
                "correlation_id": corr_id,
                "error": error,
            }

        config = _DEFAULT_RATE_LIMITS.get(endpoint_group, _DEFAULT_RATE_LIMITS["default"])
        limit = config["requests"]
        window = config["window_seconds"]

        safe_id = sanitize_redis_key(workspace_id)
        key = sanitize_redis_key(f"{safe_id}:{endpoint_group}", prefix=_REDIS_KEY_PREFIX)
        now = time.time()
        window_start = now - window

        try:
            redis = await self._get_redis()
            # Clean old entries with timeout
            await asyncio.wait_for(
                redis.zremrangebyscore(key, "-inf", window_start),
                timeout=_REDIS_TIMEOUT,
            )
            count = await asyncio.wait_for(
                redis.zcard(key),
                timeout=_REDIS_TIMEOUT,
            )

            # Get oldest entry for reset calculation
            oldest = await asyncio.wait_for(
                redis.zrange(key, 0, 0, withscores=True),
                timeout=_REDIS_TIMEOUT,
            )
            reset_in = window
            if oldest:
                oldest_ts = float(oldest[0][1])
                reset_in = max(0, int(window - (now - oldest_ts)))

            return {
                "limit": limit,
                "used": int(count),
                "remaining": max(0, limit - int(count)),
                "window_seconds": window,
                "reset_in_seconds": reset_in,
                "correlation_id": corr_id,
            }
        except Exception as e:
            logger.warning(f"[{corr_id}] Usage check failed: {e}")
            return {
                "limit": limit,
                "used": 0,
                "remaining": limit,
                "window_seconds": window,
                "reset_in_seconds": window,
                "correlation_id": corr_id,
                "error": str(e),
            }

    # ====================================================================
    # -- SYNC WRAPPERS FOR BACKWARD COMPATIBILITY -----------------------
    # ====================================================================

    def check(
        self,
        workspace_id: str,
        endpoint_group: str = "default",
        identifier: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> RateLimitResult:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_check():
            return await self.check_async(workspace_id, endpoint_group, identifier, correlation_id)

        return run_async_in_task(_do_check)

    def get_usage(
        self,
        workspace_id: str,
        endpoint_group: str = "default",
        correlation_id: Optional[str] = None,
    ) -> dict:
        """
        Sync wrapper — prefers async version in new code.
        ✅ FIXED: Use run_async_in_task helper to avoid deadlock.
        """

        async def _do_usage():
            return await self.get_usage_async(workspace_id, endpoint_group, correlation_id)

        return run_async_in_task(_do_usage)


def rate_limit_middleware(endpoint_group: str = "default"):
    """
    FastAPI dependency factory for async rate limiting.

    Usage:
        @router.post("/query")
        async def query(
            request: Request,
            _: None = Depends(rate_limit_middleware("query")),
        ):
            # Your handler code here
    """

    async def _check(request: Request):
        corr_id = request.headers.get("X-Correlation-ID") or "rate_middleware"

        # DVMELTSS-V: Get workspace_id from JWT or fallback to IP
        workspace_id = "anonymous"
        try:
            from app.auth.jwt_handler import verify_access_token

            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                payload = verify_access_token(token)
                if payload:
                    workspace_id = payload.get("workspace_id", "anonymous")
        except Exception:
            # Fallback to client IP if auth fails
            workspace_id = request.client.host if request.client else "anonymous"

        limiter = RateLimiter()
        result = await limiter.check_async(
            workspace_id=workspace_id,
            endpoint_group=endpoint_group,
            correlation_id=corr_id,
        )

        if not result.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded: {result.limit} requests per hour. "
                    f"Retry after {result.retry_after} seconds."
                ),
                headers=result.to_headers(),
            )

        # Add rate limit info to request state for logging
        request.state.rate_limit_remaining = result.remaining
        request.state.rate_limit_limit = result.limit

    return Depends(_check)


def get_rate_limiter_metadata() -> dict[str, Any]:
    """✅ NEW: Return rate limiter metadata for debugging."""
    return {
        "default_rate_limits": _DEFAULT_RATE_LIMITS,
        "redis_key_prefix": _REDIS_KEY_PREFIX,
        "fail_open": _FAIL_OPEN,
        "redis_timeout_seconds": _REDIS_TIMEOUT,
        "lua_script_sha_cached": True,
        "async_safe": True,
        "graceful_degradation": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "RateLimiter",
    "RateLimitResult",
    "rate_limit_middleware",
    "_DEFAULT_RATE_LIMITS",
    "get_rate_limiter_metadata",
]
# Local smoke test entry point. Run: python -m

