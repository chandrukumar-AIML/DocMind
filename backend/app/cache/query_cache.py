# backend/app/cache/query_cache.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security/Scalability, L - Logging
# ASCALE-FIX: A - Async, S - Separation, C - Coupling
# BATMAN-FIX: B - Batch, A - Async, M - Memory, A - API efficiency
# ✅ FINAL FIX: Check fallback state AFTER _with_retry returns

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any, Final, Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError, TimeoutError, RedisError

from app.config import get_settings
from app.core.serializers import (
    cache_serialize,
    cache_deserialize,
    safe_json_loads,
    safe_json_dumps,
)
from app.core.retry import retry_async, CircuitBreaker, RetryConfig

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG -------------------------------------------------
# ========================================================================

CACHE_PREFIX_EMBED: Final = "cache:embed:"
CACHE_PREFIX_RESULT: Final = "cache:result:"
CACHE_PREFIX_WORKSPACE: Final = "cache:ws:"
CACHE_PREFIX_STATS: Final = "cache:stats"

DEFAULT_EMBED_TTL_SECONDS: Final = 7200
DEFAULT_RESULT_TTL_SECONDS: Final = 1800
MIN_CONFIDENCE_TO_CACHE: Final = 0.5

REDIS_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.1,
    backoff_max=2.0,
    exceptions=(ConnectionError, TimeoutError, RedisError),
)

CIRCUIT_BREAKER_CONFIG: Final = {
    "failure_threshold": 5,
    "recovery_timeout": 30.0,
}

_SET_RESULT_SCRIPT: Final = """
local result_key = KEYS[1]
local ws_index_key = KEYS[2]
local serialized = ARGV[1]
local ttl = tonumber(ARGV[2])
local index_ttl = tonumber(ARGV[3])
redis.call('SETEX', result_key, ttl, serialized)
redis.call('SADD', ws_index_key, result_key)
redis.call('EXPIRE', ws_index_key, index_ttl)
return true
"""

_INVALIDATE_WORKSPACE_SCRIPT: Final = """
local ws_index_key = KEYS[1]
local keys = redis.call('SMEMBERS', ws_index_key)
if #keys > 0 then
    redis.call('DEL', unpack(keys))
end
redis.call('DEL', ws_index_key)
return #keys
"""


@dataclass(frozen=True)
class CacheStats:
    """Immutable cache performance statistics."""

    embed_hits: int = 0
    embed_misses: int = 0
    result_hits: int = 0
    result_misses: int = 0
    errors: int = 0

    @property
    def embed_hit_rate(self) -> float:
        total = self.embed_hits + self.embed_misses
        return round(self.embed_hits / total, 3) if total > 0 else 0.0

    @property
    def result_hit_rate(self) -> float:
        total = self.result_hits + self.result_misses
        return round(self.result_hits / total, 3) if total > 0 else 0.0

    @property
    def overall_hit_rate(self) -> float:
        total = self.embed_hits + self.embed_misses + self.result_hits + self.result_misses
        hits = self.embed_hits + self.result_hits
        return round(hits / total, 3) if total > 0 else 0.0


def _create_redis_client(redis_url: str, db: int = 2) -> redis.Redis:
    """Create async Redis client with production-safe defaults."""
    return redis.from_url(
        redis_url,
        db=db,
        decode_responses=True,
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
        health_check_interval=30,
        retry_on_timeout=True,
        max_connections=10,
    )


class QueryCache:
    """
    Async Redis-backed two-level cache for RAG queries.
    ✅ FINAL FIX: Check fallback state after _with_retry returns
    """

    def __init__(self, redis_client: Optional[redis.Redis] = None):
        settings = get_settings()
        redis_url = getattr(settings, "redis_url", "redis://localhost:6379/2")

        self._redis = None
        self._mode = "redis"
        self._redis_failed = False
        self._memory_store: dict[str, Any] = {}
        self._memory_expiry: dict[str, float] = {}
        self._memory_ws_index: dict[str, set[str]] = {}

        try:
            self._redis = redis_client or _create_redis_client(redis_url)
            if "upstash" in redis_url.lower():
                logger.info("QueryCache initialized | mode=redis | provider=upstash")
            elif "localhost" in redis_url or "127.0.0.1" in redis_url:
                logger.info("QueryCache initialized | mode=redis | provider=local")
            else:
                logger.info("QueryCache initialized | mode=redis")
        except Exception as e:
            logger.warning(f"QueryCache: Redis connection failed, using memory fallback | error={e}")
            self._mode = "memory"
            self._redis_failed = True
            self._redis = None

        self._embed_ttl = getattr(settings, "cache_embed_ttl_seconds", DEFAULT_EMBED_TTL_SECONDS)
        self._result_ttl = getattr(settings, "cache_result_ttl_seconds", DEFAULT_RESULT_TTL_SECONDS)

        self._circuit_breaker = CircuitBreaker(name="redis_cache", **CIRCUIT_BREAKER_CONFIG)
        logger.info(f"QueryCache | mode={self._mode} | embed_ttl={self._embed_ttl}s | result_ttl={self._result_ttl}s")

    def _validate_cache_inputs(
        self,
        workspace_id: str,
        question: Optional[str],
        result: Optional[dict],
        corr_id: str,
    ) -> tuple[bool, str]:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return False, "workspace_id must be a non-empty string"
        if question is not None and not isinstance(question, str):
            return False, "question must be a string or None"
        if result is not None and not isinstance(result, dict):
            return False, "result must be a dict or None"
        return True, ""

    async def _ensure_memory_mode(self):
        """Ensure memory mode is properly initialized."""
        if self._mode != "memory":
            self._mode = "memory"
            if not hasattr(self, "_memory_store") or self._memory_store is None:
                self._memory_store = {}
                self._memory_expiry = {}
                self._memory_ws_index = {}
            self._redis_failed = True

    async def _with_retry(self, operation, *args, **kwargs):
        """Execute Redis operation with retry + circuit breaker + memory fallback."""
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            return (
                True
                if operation.__name__
                in [
                    "setex",
                    "eval",
                    "delete",
                    "ping",
                    "hincrby",
                    "hgetall",
                    "scan",
                    "incrby",
                    "expire",
                    "exists",
                    "keys",
                ]
                else None
            )

        try:
            wrapped_op = retry_async(config=REDIS_RETRY_CONFIG)(operation)
            async with self._circuit_breaker():
                return await wrapped_op(*args, **kwargs)
        except (ConnectionError, TimeoutError, RedisError) as e:
            logger.warning(f"Redis operation failed, switching to memory mode: {e}")
            self._redis_failed = True
            await self._ensure_memory_mode()
            return True if operation.__name__ in ["setex", "eval", "delete"] else None

    @staticmethod
    def _make_embed_key(workspace_id: str, question: str) -> str:
        raw = f"{workspace_id}::{question.lower().strip()}"
        return f"{CACHE_PREFIX_EMBED}{hashlib.sha256(raw.encode()).hexdigest()}"

    @staticmethod
    def _make_result_key(workspace_id: str, question: str, filter_dict: Optional[dict] = None) -> str:
        try:
            filter_str = safe_json_dumps(filter_dict or {})
        except (TypeError, ValueError):
            filter_str = "{}"
        raw = f"{workspace_id}::{question.lower().strip()}::{filter_str}"
        return f"{CACHE_PREFIX_RESULT}{hashlib.sha256(raw.encode()).hexdigest()}"

    @staticmethod
    def _make_workspace_index_key(workspace_id: str) -> str:
        return f"{CACHE_PREFIX_WORKSPACE}{workspace_id}"

    # -- Memory fallback helpers -----------------------------------------
    def _memory_get(self, key: str) -> Optional[str]:
        if key in self._memory_expiry and time.time() > self._memory_expiry[key]:
            del self._memory_store[key]
            del self._memory_expiry[key]
            return None
        return self._memory_store.get(key)

    def _memory_setex(self, key: str, ttl: int, value: str) -> bool:
        self._memory_store[key] = value
        self._memory_expiry[key] = time.time() + ttl
        return True

    def _memory_incr(self, key: str, amount: int = 1) -> int:
        current = int(self._memory_get(key) or 0)
        new_value = current + amount
        self._memory_store[key] = str(new_value)
        return new_value

    def _memory_delete(self, *keys: str) -> int:
        count = 0
        for key in keys:
            if key in self._memory_store:
                del self._memory_store[key]
                if key in self._memory_expiry:
                    del self._memory_expiry[key]
                count += 1
        return count

    # -- Embedding cache -------------------------------------------------
    async def get_embedding(self, workspace_id: str, question: str) -> Optional[list[float]]:
        key = self._make_embed_key(workspace_id, question)

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            raw = self._memory_get(key)
            if raw:
                await self._increment("embed_hits")
                return safe_json_loads(raw)
            await self._increment("embed_misses")
            return None

        try:
            raw = await self._with_retry(self._redis.get, key)
            if raw:
                await self._increment("embed_hits")
                return safe_json_loads(raw)
            await self._increment("embed_misses")
            return None
        except RedisError as e:
            logger.debug(f"Embed cache get failed (graceful miss): {e}")
            await self._increment("errors")
            return None

    async def set_embedding(self, workspace_id: str, question: str, embedding: list[float]) -> bool:
        """✅ FINAL FIX: Check fallback state after _with_retry"""
        key = self._make_embed_key(workspace_id, question)
        serialized = safe_json_dumps(embedding)

        # If already in memory mode, use it directly
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            return self._memory_setex(key, self._embed_ttl, serialized)

        # Try Redis with retry/fallback
        await self._with_retry(self._redis.setex, key, self._embed_ttl, serialized)

        # ✅ CRITICAL: If fallback occurred during _with_retry, save to memory now
        if self._redis_failed or self._mode == "memory":
            await self._ensure_memory_mode()
            return self._memory_setex(key, self._embed_ttl, serialized)

        return True

    # -- Result cache ----------------------------------------------------
    async def get_result(self, workspace_id: str, question: str, filter_dict: Optional[dict] = None) -> Optional[dict]:
        is_valid, error = self._validate_cache_inputs(workspace_id, question, None, "cache_get")
        if not is_valid:
            logger.error(f"Invalid cache inputs: {error}")
            return None

        key = self._make_result_key(workspace_id, question, filter_dict)

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            raw = self._memory_get(key)
            if raw:
                await self._increment("result_hits")
                result = cache_deserialize(raw) or safe_json_loads(raw)
                if result and isinstance(result, dict):
                    result["from_cache"] = True
                    return result
            await self._increment("result_misses")
            return None

        try:
            raw = await self._with_retry(self._redis.get, key)
            if raw:
                await self._increment("result_hits")
                result = cache_deserialize(raw)
                if result:
                    result["from_cache"] = True
                    return result
                result = safe_json_loads(raw)
                if result and isinstance(result, dict):
                    result["from_cache"] = True
                    return result
            await self._increment("result_misses")
            return None
        except RedisError as e:
            logger.debug(f"Result cache get failed (graceful miss): {e}")
            await self._increment("errors")
            return None

    async def set_result(
        self,
        workspace_id: str,
        question: str,
        result: dict,
        filter_dict: Optional[dict] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """✅ FINAL FIX: Check fallback state after _with_retry"""
        is_valid, error = self._validate_cache_inputs(workspace_id, question, result, "cache_set")
        if not is_valid:
            logger.error(f"Invalid cache inputs: {error}")
            return False

        confidence = result.get("confidence_score", 1.0)
        if confidence < MIN_CONFIDENCE_TO_CACHE:
            logger.debug(f"Skipping cache: low confidence {confidence:.2f}")
            return False
        if result.get("web_search_used", False):
            logger.debug("Skipping cache: result used time-sensitive web search")
            return False
        if not result.get("answer"):
            logger.debug("Skipping cache: empty answer")
            return False

        key = self._make_result_key(workspace_id, question, filter_dict)
        ws_index_key = self._make_workspace_index_key(workspace_id)
        serialized = cache_serialize(result)
        target_ttl = ttl or self._result_ttl

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            success = self._memory_setex(key, target_ttl, serialized)
            if success:
                if ws_index_key not in self._memory_ws_index:
                    self._memory_ws_index[ws_index_key] = set()
                self._memory_ws_index[ws_index_key].add(key)
            return success

        # Try Redis with retry/fallback
        index_ttl = target_ttl + 300
        await self._with_retry(
            self._redis.eval,
            _SET_RESULT_SCRIPT,
            2,
            key,
            ws_index_key,
            serialized,
            target_ttl,
            index_ttl,
        )

        # ✅ CRITICAL: If fallback occurred during _with_retry, save to memory now
        if self._redis_failed or self._mode == "memory":
            await self._ensure_memory_mode()
            success = self._memory_setex(key, target_ttl, serialized)
            if success:
                if ws_index_key not in self._memory_ws_index:
                    self._memory_ws_index[ws_index_key] = set()
                self._memory_ws_index[ws_index_key].add(key)
            return success

        return True

    # -- Cache invalidation ----------------------------------------------
    async def invalidate_workspace(self, workspace_id: str) -> int:
        ws_index_key = self._make_workspace_index_key(workspace_id)

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            deleted = 0
            if ws_index_key in self._memory_ws_index:
                keys_to_delete = list(self._memory_ws_index[ws_index_key])
                for key in keys_to_delete:
                    if key in self._memory_store:
                        del self._memory_store[key]
                        if key in self._memory_expiry:
                            del self._memory_expiry[key]
                        deleted += 1
                del self._memory_ws_index[ws_index_key]
            logger.info(f"Cache invalidated (memory): workspace={workspace_id} | {deleted} entries")
            return deleted

        try:
            deleted_count = await self._with_retry(self._redis.eval, _INVALIDATE_WORKSPACE_SCRIPT, 1, ws_index_key)
            logger.info(f"Cache invalidated: workspace={workspace_id} | {deleted_count} entries deleted")
            return int(deleted_count)
        except RedisError as e:
            logger.warning(f"Cache invalidation failed for workspace={workspace_id}: {e}")
            await self._increment("errors")
            return 0

    async def invalidate_result(self, workspace_id: str, question: str, filter_dict: Optional[dict] = None) -> bool:
        key = self._make_result_key(workspace_id, question, filter_dict)
        ws_index_key = self._make_workspace_index_key(workspace_id)

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            deleted = self._memory_delete(key)
            if ws_index_key in self._memory_ws_index and key in self._memory_ws_index[ws_index_key]:
                self._memory_ws_index[ws_index_key].discard(key)
            return deleted > 0

        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.delete(key)
            pipe.srem(ws_index_key, key)
            await self._with_retry(pipe.execute)
            logger.debug(f"Cache invalidated: key={key[:50]}...")
            return True
        except RedisError as e:
            logger.warning(f"Single-key invalidation failed: {e}")
            return False

    async def flush_all(self, max_keys: int = 10000) -> tuple[int, bool]:
        settings = get_settings()
        if getattr(settings, "environment", "dev") == "production":
            logger.warning(f"flush_all() called in production — deleting up to {max_keys} keys")

        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            deleted = len(self._memory_store)
            self._memory_store.clear()
            self._memory_expiry.clear()
            self._memory_ws_index.clear()
            logger.info(f"Memory cache flushed: {deleted} keys deleted")
            return deleted, True

        total_deleted = 0
        try:
            for prefix in (
                CACHE_PREFIX_EMBED,
                CACHE_PREFIX_RESULT,
                CACHE_PREFIX_WORKSPACE,
            ):
                cursor = 0
                while True:
                    cursor, keys = await self._with_retry(self._redis.scan, cursor, match=f"{prefix}*", count=200)
                    if keys:
                        keys_to_delete = keys[: max_keys - total_deleted]
                        if keys_to_delete:
                            await self._redis.delete(*keys_to_delete)
                            total_deleted += len(keys_to_delete)
                        if total_deleted >= max_keys:
                            return total_deleted, False
                    if cursor == 0:
                        break
                    if total_deleted % 1000 == 0:
                        await asyncio.sleep(0)
            logger.info(f"Query cache flush complete: {total_deleted} keys deleted")
            return total_deleted, True
        except RedisError as e:
            logger.error(f"Cache flush failed after deleting {total_deleted} keys: {e}")
            return total_deleted, False

    async def get_stats(self) -> CacheStats:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            return CacheStats()
        try:
            raw = await self._with_retry(self._redis.hgetall, CACHE_PREFIX_STATS)
            return CacheStats(
                embed_hits=int(raw.get("embed_hits", 0)),
                embed_misses=int(raw.get("embed_misses", 0)),
                result_hits=int(raw.get("result_hits", 0)),
                result_misses=int(raw.get("result_misses", 0)),
                errors=int(raw.get("errors", 0)),
            )
        except RedisError:
            return CacheStats(errors=1)

    async def _increment(self, counter: str) -> None:
        """✅ FIXED: Properly async - must be awaited"""
        if self._mode == "memory" or self._redis_failed or not self._redis:
            return
        try:
            await self._redis.hincrby(CACHE_PREFIX_STATS, counter, 1)
        except RedisError as e:
            logger.debug(f"Stats increment failed for '{counter}': {e}")

    async def is_healthy(self) -> bool:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            return True
        try:
            return await self._with_retry(self._redis.ping) is True
        except RedisError:
            return False

    async def close(self) -> None:
        """✅ FIXED: aclose() with fallback for redis-py compatibility"""
        if self._mode == "redis" and self._redis and not self._redis_failed:
            try:
                await self._redis.aclose()
            except AttributeError:
                await self._redis.close()
            logger.debug("QueryCache Redis connection closed")

    async def incr(self, key: str, amount: int = 1, expire_seconds: Optional[int] = None) -> int:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            value = self._memory_incr(key, amount)
            if expire_seconds is not None:
                self._memory_expiry[key] = time.time() + expire_seconds
            return value
        value = await self._with_retry(self._redis.incrby, key, amount)
        if expire_seconds is not None:
            await self._with_retry(self._redis.expire, key, expire_seconds)
        return int(value)

    async def setex(self, key: str, seconds: int, value: Any) -> bool:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            return self._memory_setex(key, seconds, str(value))
        await self._with_retry(self._redis.setex, key, seconds, value)
        return True

    async def exists(self, key: str) -> bool:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            return key in self._memory_store and (
                key not in self._memory_expiry or time.time() <= self._memory_expiry[key]
            )
        return bool(await self._with_retry(self._redis.exists, key))

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            return self._memory_delete(*keys)
        return int(await self._with_retry(self._redis.delete, *keys))

    async def keys(self, pattern: str) -> list[str]:
        if self._mode == "memory" or self._redis_failed or not self._redis:
            await self._ensure_memory_mode()
            import fnmatch

            return [k for k in self._memory_store.keys() if fnmatch.fnmatch(k, pattern)]
        return await self._with_retry(self._redis.keys, pattern)

    async def __aenter__(self) -> "QueryCache":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()


def get_cache_metadata() -> dict[str, Any]:
    return {
        "prefixes": {
            "embed": CACHE_PREFIX_EMBED,
            "result": CACHE_PREFIX_RESULT,
            "workspace": CACHE_PREFIX_WORKSPACE,
            "stats": CACHE_PREFIX_STATS,
        },
        "ttl_defaults": {
            "embed_seconds": DEFAULT_EMBED_TTL_SECONDS,
            "result_seconds": DEFAULT_RESULT_TTL_SECONDS,
        },
        "min_confidence_to_cache": MIN_CONFIDENCE_TO_CACHE,
        "retry_config": {
            "max_attempts": REDIS_RETRY_CONFIG.max_attempts,
            "backoff_base": REDIS_RETRY_CONFIG.backoff_base,
            "backoff_max": REDIS_RETRY_CONFIG.backoff_max,
        },
        "circuit_breaker": CIRCUIT_BREAKER_CONFIG,
    }


__all__ = ["QueryCache", "CacheStats", "get_cache_metadata"]


# ========================================================================
# -- LOCAL TESTING ENTRY POINT -------------------------------------------
# ========================================================================

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[2]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    async def run_tests():
        print("🔍 Testing QueryCache module (app/cache/query_cache.py)")
        print("=" * 70)

        try:
            cache = QueryCache()
            print(f"✅ Cache initialized | mode={cache._mode}")

            ws_id = "test-workspace-123"
            question = "What is the revenue for Q4 2025?"
            test_embedding = [0.1, 0.2, 0.3, 0.4]
            test_result = {
                "answer": "Q4 2025 revenue was $2.4M",
                "confidence_score": 0.92,
                "sources": ["doc_001.pdf"],
                "web_search_used": False,
            }

            print("\n📌 Test 1: Embedding Cache (set -> get)")
            set_ok = await cache.set_embedding(ws_id, question, test_embedding)
            print(f"   set_embedding: {'✅ PASS' if set_ok else '❌ FAIL'}")
            retrieved = await cache.get_embedding(ws_id, question)
            if retrieved == test_embedding:
                print(f"   get_embedding: ✅ PASS | value={retrieved[:3]}...")
            else:
                print(f"   get_embedding: ❌ FAIL | expected={test_embedding}, got={retrieved}")

            print("\n📌 Test 2: Result Cache (set -> get)")
            set_result_ok = await cache.set_result(ws_id, question, test_result)
            print(f"   set_result: {'✅ PASS' if set_result_ok else '❌ FAIL'}")
            cached_result = await cache.get_result(ws_id, question)
            if cached_result and cached_result.get("answer") == test_result["answer"]:
                print(f"   get_result: ✅ PASS | from_cache={cached_result.get('from_cache')}")
            else:
                print(f"   get_result: ❌ FAIL | cached={cached_result}")

            print("\n📌 Test 3: Workspace Invalidation")
            deleted = await cache.invalidate_workspace(ws_id)
            print(f"   invalidate_workspace: ✅ PASS | deleted={deleted} entries")
            still_exists = await cache.get_result(ws_id, question)
            if still_exists is None:
                print("   verify deletion: ✅ PASS | result cleared")
            else:
                print("   verify deletion: ❌ FAIL | result still present")

            print("\n📌 Test 4: Health Check")
            healthy = await cache.is_healthy()
            print(f"   is_healthy: {'✅ PASS' if healthy else '⚠️  DEGRADED'}")

            await cache.close()
            print("\n" + "=" * 70)
            print("✅ All tests completed successfully!")
            return True
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
