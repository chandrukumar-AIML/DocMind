
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Optional

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.redis_utils import get_async_redis, sanitize_redis_key
from app.core.celery_utils import (
    run_async_in_task,
)  # ✅ NEW: For safe async execution in Celery
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    UPLOADING = "uploading"
    OCR = "ocr"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    GRAPH = "graph"
    VERSIONING = "versioning"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class _ProgressEventEncoder(json.JSONEncoder):
    """Handle non-serializable types in ProgressEvent details."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, (time.struct_time,)):
            return time.mktime(obj)
        if hasattr(obj, "isoformat"):  # datetime, UUID, etc.
            return obj.isoformat()
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return super().default(obj)


@dataclass(frozen=True)
class ProgressEvent:
    """
    A single progress event published to Redis.
    Consumed by the WebSocket endpoint and forwarded to the browser.
    FIXED: Frozen for immutability + added correlation_id.
    """

    task_id: str
    status: str
    stage: str
    message: str
    progress: float  # 0.0–100.0
    timestamp: float
    filename: str = ""
    details: dict = field(default_factory=dict)
    correlation_id: Optional[str] = None

    # Completion details
    page_count: int = 0
    chunk_count: int = 0
    latency_seconds: float = 0.0
    error: Optional[str] = None

    def to_json(self) -> str:
        d = {
            "task_id": self.task_id,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": round(self.progress, 1),
            "timestamp": self.timestamp,
            "filename": self.filename,
            "details": self.details,
            "correlation_id": self.correlation_id,
        }
        if self.status == TaskStatus.COMPLETE:
            d.update(
                {
                    "page_count": self.page_count,
                    "chunk_count": self.chunk_count,
                    "latency_seconds": round(self.latency_seconds, 2),
                }
            )
        if self.status == TaskStatus.FAILED:
            d["error"] = self.error
        return json.dumps(d, cls=_ProgressEventEncoder)


def _validate_progress_inputs(
    task_id: str,
    status: str,
    stage: str,
    filename: str,
    corr_id: str,
) -> tuple[bool, str]:
    """Validate inputs before publishing progress."""
    if not isinstance(task_id, str) or not task_id.strip():
        return False, "task_id must be a non-empty string"
    if not isinstance(status, str) or not status.strip():
        return False, "status must be a non-empty string"
    if not isinstance(stage, str) or not stage.strip():
        return False, "stage must be a non-empty string"
    if filename is not None and not isinstance(filename, str):
        return False, "filename must be a string or None"
    return True, ""


class ProgressPublisher:
    """
    Publishes task progress events to Redis.
    Used inside Celery tasks to report progress.

    Each event is:
    1. Published to channel task:{task_id} (real-time WebSocket delivery)
    2. Stored as task_progress:{task_id} (catch-up for late subscribers)
    3. Added to task_history:{task_id} list (full event log)

    FIXED: Uses async Redis via centralized utilities.
    """

    CHANNEL_PREFIX: Final = "task"
    STATE_PREFIX: Final = "task_progress"
    HISTORY_PREFIX: Final = "task_history"
    TTL_SECONDS: Final = 600  # 10 minutes

    # Retry config for Redis operations
    _REDIS_RETRY_CONFIG: Final = RetryConfig(
        max_attempts=3,
        backoff_base=0.5,
        backoff_max=5.0,
        exceptions=(Exception,),
    )

    def __init__(self, redis_url: Optional[str] = None):
        settings = get_settings()
        self.redis_url = redis_url or getattr(settings, "redis_url", "redis://localhost:6379/2")
        self._redis: Optional[Any] = None

    async def _get_redis(self) -> Any:
        """Lazy-load async Redis connection."""
        if self._redis is None:
            self._redis = await get_async_redis(self.redis_url, db=2)
        return self._redis

    async def publish_async(
        self,
        task_id: str,
        status: TaskStatus | str,
        stage: str,
        message: str,
        progress: float,
        filename: str = "",
        details: Optional[dict] = None,
        correlation_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Async: Publish a progress event."""
        # ✅ Validate inputs
        is_valid, error = _validate_progress_inputs(task_id, str(status), stage, filename, correlation_id or "progress")
        if not is_valid:
            logger.error(f"[{correlation_id or 'progress'}] Invalid inputs: {error}")
            return

        event = ProgressEvent(
            task_id=task_id,
            status=str(status),
            stage=stage,
            message=message,
            progress=progress,
            timestamp=time.time(),
            filename=filename,
            details=details or {},
            correlation_id=correlation_id,
            **kwargs,
        )
        event_json = event.to_json()
        corr_id = correlation_id or "progress_unknown"

        try:
            redis = await self._get_redis()

            channel_key = sanitize_redis_key(f"{self.CHANNEL_PREFIX}:{task_id}")
            state_key = sanitize_redis_key(f"{self.STATE_PREFIX}:{task_id}")
            history_key = sanitize_redis_key(f"{self.HISTORY_PREFIX}:{task_id}")

            @retry_async(config=self._REDIS_RETRY_CONFIG)
            async def _publish():
                pipe = redis.pipeline()
                pipe.publish(channel_key, event_json)
                pipe.setex(state_key, self.TTL_SECONDS, event_json)
                pipe.rpush(history_key, event_json)
                pipe.expire(history_key, self.TTL_SECONDS)
                await pipe.execute()

            await _publish()

        except Exception as e:
            logger.warning(f"[{corr_id}] Progress publish failed: {e}")

    def publish(
        self,
        task_id: str,
        status: TaskStatus | str,
        stage: str,
        message: str,
        progress: float,
        filename: str = "",
        details: Optional[dict] = None,
        correlation_id: Optional[str] = None,
        **kwargs,
    ) -> None:
        """
        Sync wrapper for Celery tasks.
        ✅ FIXED: Use run_async_in_task helper for safe async execution.
        """

        async def _do_publish():
            await self.publish_async(
                task_id,
                status,
                stage,
                message,
                progress,
                filename,
                details,
                correlation_id,
                **kwargs,
            )

        # ✅ Use centralized helper to avoid deadlock in Celery
        run_async_in_task(_do_publish)

    async def get_current_state_async(self, task_id: str) -> Optional[dict]:
        """Async: Get the most recent state for a task."""
        try:
            redis = await self._get_redis()
            key = sanitize_redis_key(f"{self.STATE_PREFIX}:{task_id}")
            raw = await redis.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning(f"State fetch failed: {e}")
            return None

    def get_current_state(self, task_id: str) -> Optional[dict]:
        """
        Sync wrapper for HTTP polling fallback.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_get():
            return await self.get_current_state_async(task_id)

        return run_async_in_task(_do_get)

    async def get_history_async(self, task_id: str) -> list[dict]:
        """Async: Get full event history for a task."""
        try:
            redis = await self._get_redis()
            key = sanitize_redis_key(f"{self.HISTORY_PREFIX}:{task_id}")
            events = await redis.lrange(key, 0, -1)
            return [json.loads(e) for e in events]
        except Exception:
            return []

    def get_history(self, task_id: str) -> list[dict]:
        """
        Sync wrapper.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_get():
            return await self.get_history_async(task_id)

        return run_async_in_task(_do_get)

    def complete(
        self,
        task_id: str,
        filename: str,
        page_count: int,
        chunk_count: int,
        latency_seconds: float,
        correlation_id: Optional[str] = None,
    ):
        """Publish a completion event."""
        self.publish(
            task_id=task_id,
            status=TaskStatus.COMPLETE,
            stage="complete",
            message=f"Indexed: {page_count} pages, {chunk_count} chunks",
            progress=100.0,
            filename=filename,
            page_count=page_count,
            chunk_count=chunk_count,
            latency_seconds=latency_seconds,
            correlation_id=correlation_id,
        )

    def fail(
        self,
        task_id: str,
        filename: str,
        error: str,
        correlation_id: Optional[str] = None,
    ):
        """Publish a failure event."""
        self.publish(
            task_id=task_id,
            status=TaskStatus.FAILED,
            stage="failed",
            message=f"Processing failed: {error[:200]}",
            progress=0.0,
            filename=filename,
            error=error,
            correlation_id=correlation_id,
        )


class ProgressSubscriber:
    """
    Subscribes to task progress events from Redis.
    Used by the WebSocket endpoint to stream events to browsers.
    """

    def __init__(self, redis_url: Optional[str] = None):
        settings = get_settings()
        self.redis_url = redis_url or getattr(settings, "redis_url", "redis://localhost:6379/2")

    def subscribe(self, task_id: str):
        """
        Subscribe to progress events for a task.
        Returns a Redis pubsub object for iteration.
        ✅ FIXED: Handle connection errors gracefully.
        """
        import redis as redis_sync

        try:
            redis = redis_sync.from_url(self.redis_url, decode_responses=True)
            pubsub = redis.pubsub(ignore_subscribe_messages=True)
            channel = sanitize_redis_key(f"{ProgressPublisher.CHANNEL_PREFIX}:{task_id}")
            pubsub.subscribe(channel)
            return pubsub
        except Exception as e:
            logger.warning(f"Failed to subscribe to task {task_id}: {e}")
            return None

    async def get_current_state_async(self, task_id: str) -> Optional[dict]:
        """Async: Get current state without subscribing."""
        try:
            redis = await get_async_redis(self.redis_url, db=2)
            key = sanitize_redis_key(f"{ProgressPublisher.STATE_PREFIX}:{task_id}")
            raw = await redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def get_current_state(self, task_id: str) -> Optional[dict]:
        """
        Sync wrapper for HTTP polling.
        ✅ FIXED: Use run_async_in_task helper.
        """

        async def _do_get():
            return await self.get_current_state_async(task_id)

        return run_async_in_task(_do_get)


def get_progress_metadata() -> dict[str, Any]:
    """✅ NEW: Return progress metadata for monitoring."""
    return {
        "ttl_seconds": ProgressPublisher.TTL_SECONDS,
        "channel_prefix": ProgressPublisher.CHANNEL_PREFIX,
        "state_prefix": ProgressPublisher.STATE_PREFIX,
        "history_prefix": ProgressPublisher.HISTORY_PREFIX,
        "retry_config": {
            "max_attempts": ProgressPublisher._REDIS_RETRY_CONFIG.max_attempts,
            "backoff_base": ProgressPublisher._REDIS_RETRY_CONFIG.backoff_base,
            "backoff_max": ProgressPublisher._REDIS_RETRY_CONFIG.backoff_max,
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "ProgressPublisher",
    "ProgressSubscriber",
    "TaskStatus",
    "ProgressEvent",
    "get_progress_metadata",
]
# Local smoke test entry point. Run: python -m

