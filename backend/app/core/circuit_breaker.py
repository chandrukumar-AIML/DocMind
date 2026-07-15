"""
Async circuit breaker for external service calls (LLM, embedding APIs).

States:
  CLOSED   — normal operation, calls pass through
  OPEN     — too many failures, calls rejected immediately until reset_timeout_s elapses
  HALF_OPEN — one probe call allowed; if it succeeds → CLOSED, if it fails → OPEN again

Usage:
    _breaker = CircuitBreaker("openai-llm", failure_threshold=5, reset_timeout_s=60)

    async def call_llm():
        async with _breaker:
            return await llm.ainvoke(messages)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Type

logger = logging.getLogger(__name__)


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker '{name}' is OPEN — service unavailable. "
            f"Retry in {retry_after:.1f}s."
        )


@dataclass
class CircuitBreaker:
    """
    Thread-safe async circuit breaker backed by asyncio.Lock.

    Args:
        name: Human-readable name for logs/metrics.
        failure_threshold: Consecutive failures before opening (default 5).
        reset_timeout_s: Seconds to wait before probing in HALF_OPEN (default 60).
        success_threshold: Consecutive successes in HALF_OPEN to re-close (default 2).
        excluded_exceptions: Exception types that do NOT count as failures
                             (e.g. validation errors — caller's fault, not the service).
    """

    name: str
    failure_threshold: int = 5
    reset_timeout_s: float = 60.0
    success_threshold: int = 2
    excluded_exceptions: tuple[Type[BaseException], ...] = field(default_factory=tuple)

    _state: _State = field(default=_State.CLOSED, init=False, repr=False)
    _failure_count: int = field(default=0, init=False, repr=False)
    _success_count: int = field(default=0, init=False, repr=False)
    _opened_at: Optional[float] = field(default=None, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # ── public state ──────────────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state.value

    @property
    def is_open(self) -> bool:
        return self._state == _State.OPEN

    # ── context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "CircuitBreaker":
        async with self._lock:
            await self._check_state()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is not None:
            if self.excluded_exceptions and issubclass(exc_type, self.excluded_exceptions):
                return False  # don't count caller errors, don't suppress
            async with self._lock:
                await self._on_failure()
            return False  # let the exception propagate
        async with self._lock:
            await self._on_success()
        return False

    # ── internal state machine ────────────────────────────────────────────────

    async def _check_state(self) -> None:
        if self._state == _State.CLOSED:
            return

        if self._state == _State.OPEN:
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            remaining = self.reset_timeout_s - elapsed
            if remaining > 0:
                raise CircuitBreakerOpen(self.name, retry_after=remaining)
            # Transition to HALF_OPEN — allow one probe
            self._state = _State.HALF_OPEN
            self._success_count = 0
            logger.info(f"[CircuitBreaker:{self.name}] → HALF_OPEN (probe)")
            return

        # HALF_OPEN: probe is already in flight — reject concurrent callers
        if self._state == _State.HALF_OPEN:
            elapsed = time.monotonic() - (self._opened_at or 0.0)
            remaining = max(0.0, self.reset_timeout_s - elapsed)
            raise CircuitBreakerOpen(self.name, retry_after=remaining)

    async def _on_success(self) -> None:
        if self._state == _State.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = _State.CLOSED
                self._failure_count = 0
                self._opened_at = None
                logger.info(f"[CircuitBreaker:{self.name}] → CLOSED (recovered)")
        elif self._state == _State.CLOSED:
            self._failure_count = 0  # reset on any success

    async def _on_failure(self) -> None:
        self._failure_count += 1
        if self._state == _State.HALF_OPEN or self._failure_count >= self.failure_threshold:
            self._state = _State.OPEN
            self._opened_at = time.monotonic()
            self._success_count = 0
            logger.error(
                f"[CircuitBreaker:{self.name}] → OPEN "
                f"(failures={self._failure_count}, threshold={self.failure_threshold})"
            )

    # ── manual controls ────────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Force the circuit back to CLOSED — useful for operator remediation."""
        async with self._lock:
            self._state = _State.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
            logger.info(f"[CircuitBreaker:{self.name}] manually reset → CLOSED")

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "opened_at": self._opened_at,
        }
