"""Shared Redis helpers for async services.

# ADDED: Centralized Redis client and Lua helpers used by rate limiting and
# task progress tracking.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Sequence

import redis.asyncio as redis

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9:._-]+")


def sanitize_redis_key(value: str, prefix: str | None = None, max_length: int = 256) -> str:
    """Return a Redis-safe key segment with optional namespace prefix."""
    cleaned = _SAFE_KEY_RE.sub("_", str(value).strip())
    cleaned = cleaned.strip(":._-") or "anonymous"
    key = f"{prefix}:{cleaned}" if prefix else cleaned
    return key[:max_length]


@lru_cache(maxsize=16)
def _client_for(redis_url: str, db: int) -> redis.Redis:
    return redis.from_url(
        redis_url,
        db=db,
        decode_responses=True,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
        health_check_interval=30,
        retry_on_timeout=True,
        max_connections=20,
    )


async def get_async_redis(redis_url: str, db: int = 0) -> redis.Redis:
    """Return a pooled async Redis client."""
    return _client_for(redis_url, db)


async def load_lua_script(client: redis.Redis, script: str) -> str:
    """Load a Lua script and return its SHA."""
    return await client.script_load(script)


async def safe_evalsha(
    client: redis.Redis,
    sha: str | None,
    *,
    keys: Sequence[str] | None = None,
    args: Sequence[Any] | None = None,
) -> Any:
    """Run a loaded Lua script with a typed, narrow interface."""
    # OPTIMIZED: Avoid ad hoc eval argument construction at call sites.
    if not sha:
        raise ValueError("Lua script SHA is required")
    redis_keys = list(keys or [])
    redis_args = [str(arg) for arg in (args or [])]
    return await client.evalsha(sha, len(redis_keys), *(redis_keys + redis_args))


__all__ = [
    "get_async_redis",
    "sanitize_redis_key",
    "load_lua_script",
    "safe_evalsha",
]
# Local smoke test entry point. Run: python -m

