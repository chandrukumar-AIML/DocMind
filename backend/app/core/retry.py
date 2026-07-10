"""
Async retry utilities with circuit breaker support.

Provides non-blocking retry logic for Redis, HTTP, and other I/O operations.

Usage:
    from app.core.retry import retry_async, CircuitBreaker

    @retry_async(max_attempts=3)
    async def fetch_data(): ...

    cb = CircuitBreaker(failure_threshold=5)
    async with cb:
        result = await external_api.call()
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Awaitable, Callable, Deque, Optional, TypeVar

# Import Redis exceptions properly for preset configs
try:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError
    from redis.exceptions import RedisError as RedisBaseError

    REDIS_EXCEPTIONS = (RedisConnectionError, RedisTimeoutError, RedisBaseError)
except ImportError:
    # Fallback if redis isn't installed yet (dev setup phase)
    REDIS_EXCEPTIONS = (Exception,)

try:
    import httpx

    HTTP_EXCEPTIONS = (
        httpx.RequestError,
        httpx.TimeoutException,
        httpx.HTTPStatusError,
    )
except ImportError:
    HTTP_EXCEPTIONS = (Exception,)

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for async retry logic."""

    max_attempts: int = 3
    backoff_base: float = 0.1  # seconds
    backoff_max: float = 5.0  # cap exponential growth
    jitter: bool = True  # add randomness to prevent thundering herd
    exceptions: tuple[type[Exception], ...] = (Exception,)  # which errors to retry


def retry_async(
    func: Optional[Callable[..., Awaitable[T]]] = None,
    *,
    config: Optional[RetryConfig] = None,
) -> Callable[..., Awaitable[T]]:
    """
    Async retry decorator with exponential backoff + jitter.

    Usage:
        @retry_async()
        async def my_func(): ...

        # Or with config:
        @retry_async(config=RetryConfig(max_attempts=5))
        async def my_func(): ...
    """
    cfg = config or RetryConfig()

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            last_error = None
            for attempt in range(cfg.max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except cfg.exceptions as e:
                    last_error = e
                    if attempt == cfg.max_attempts - 1:
                        break

                    # Calculate backoff with optional jitter
                    delay = min(cfg.backoff_base * (2**attempt), cfg.backoff_max)
                    if cfg.jitter:
                        delay *= 0.5 + random.random()

                    logger.debug(
                        f"{fn.__name__} attempt {attempt+1}/{cfg.max_attempts} failed: {e}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)

            logger.error(f"{fn.__name__} failed after {cfg.max_attempts} attempts: {last_error}")
            raise last_error

        return wrapper

    # Handle both @retry_async and @retry_async() syntax
    if func is not None and callable(func):
        return decorator(func)
    return decorator


@dataclass
class CircuitBreaker:
    """
    Simple circuit breaker for async operations.

    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failure threshold exceeded, requests fail fast
    - HALF_OPEN: Testing if service recovered

    Usage:
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)

        async def safe_call():
            async with cb:
                return await external_service.call()
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0  # seconds
    name: str = "default"

    _failures: Deque[float] = field(default_factory=deque)
    _state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    _last_failure_time: Optional[float] = None

    @property
    def is_open(self) -> bool:
        """Check if circuit is OPEN (failing fast)."""
        if self._state == "CLOSED":
            return False
        if self._state == "OPEN":
            # Check if recovery timeout has passed
            if self._last_failure_time and time.time() - self._last_failure_time > self.recovery_timeout:
                self._state = "HALF_OPEN"
                logger.info(f"CircuitBreaker[{self.name}] -> HALF_OPEN (testing recovery)")
                return False
            return True
        return False  # HALF_OPEN allows one request through

    def record_success(self) -> None:
        """Record successful call — reset failure tracking."""
        if self._state == "HALF_OPEN":
            self._state = "CLOSED"
            self._failures.clear()
            logger.info(f"CircuitBreaker[{self.name}] -> CLOSED (recovered)")

    def record_failure(self) -> None:
        """Record failed call — track for threshold check."""
        now = time.time()
        self._failures.append(now)
        self._last_failure_time = now

        # Remove old failures outside time window
        cutoff = now - self.recovery_timeout
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()

        # Check threshold
        if len(self._failures) >= self.failure_threshold:
            self._state = "OPEN"
            logger.warning(
                f"CircuitBreaker[{self.name}] -> OPEN " f"({len(self._failures)} failures in {self.recovery_timeout}s)"
            )

    @asynccontextmanager
    async def __call__(self):
        """Context manager for circuit breaker protection."""
        if self.is_open:
            raise RuntimeError(f"CircuitBreaker[{self.name}] is OPEN — failing fast")

        try:
            yield
            self.record_success()
        except Exception:
            self.record_failure()
            raise


# DVMELTSS-M: Pre-configured retry presets for common use cases
redis_retry = retry_async(
    config=RetryConfig(
        max_attempts=3,
        backoff_base=0.1,
        backoff_max=2.0,
        exceptions=REDIS_EXCEPTIONS,
    )
)

http_retry = retry_async(
    config=RetryConfig(
        max_attempts=5,
        backoff_base=0.5,
        backoff_max=10.0,
        exceptions=HTTP_EXCEPTIONS,
    )
)


# DVMELTSS-M: Explicit module exports
__all__ = [
    "RetryConfig",
    "retry_async",
    "CircuitBreaker",
    "redis_retry",
    "http_retry",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.retry) ----------
# ========================================================================

