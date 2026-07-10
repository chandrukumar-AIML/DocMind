
from __future__ import annotations

import logging
from typing import Any, Final

from celery import Celery
from celery.signals import worker_ready, worker_shutdown
from kombu import Queue, Exchange

# DVMELTSS-M: Import centralized config
from app.config import get_settings

logger = logging.getLogger(__name__)

# -- Queue definitions ---------------------------------------------------------
DEFAULT_EXCHANGE = Exchange("documind", type="direct")

QUEUES: Final = (
    Queue(
        "high_priority",
        DEFAULT_EXCHANGE,
        routing_key="high_priority",
        queue_arguments={"x-max-priority": 10},
    ),
    Queue(
        "default",
        DEFAULT_EXCHANGE,
        routing_key="default",
        queue_arguments={"x-max-priority": 5},
    ),
    Queue(
        "bulk",
        DEFAULT_EXCHANGE,
        routing_key="bulk",
        queue_arguments={"x-max-priority": 1},
    ),
)

QUEUE_TIER_MAP: Final = {
    "high": "high_priority",
    "default": "default",
    "bulk": "bulk",
}


def _on_task_failure(exc: Exception, task_id: str, args: tuple, kwargs: dict, einfo: Any) -> None:
    """Log task failures with correlation_id context."""
    corr_id = kwargs.get("correlation_id", "unknown")
    logger.error(f"[{corr_id}] Task {task_id} failed: {exc}")


def _validate_celery_config(broker_url: str, backend_url: str) -> tuple[bool, str]:
    """Validate Celery broker and backend URLs."""
    if not broker_url or not isinstance(broker_url, str):
        return False, "broker_url must be a non-empty string"
    if not backend_url or not isinstance(backend_url, str):
        return False, "backend_url must be a non-empty string"
    # Basic URL format check
    if not broker_url.startswith(("redis://", "amqp://", "sqs://")):
        return False, f"Unsupported broker protocol in: {broker_url}"
    if not backend_url.startswith(("redis://", "rpc://", "db+")):
        return False, f"Unsupported backend protocol in: {backend_url}"
    return True, ""


def create_celery_app() -> Celery:
    """Create and configure the Celery application."""
    settings = get_settings()
    broker_url = getattr(settings, "celery_broker_url", "redis://localhost:6379/0")
    backend_url = getattr(settings, "celery_result_backend", "redis://localhost:6379/1")

    # ✅ Validate config before creating app
    is_valid, error = _validate_celery_config(broker_url, backend_url)
    if not is_valid:
        logger.error(f"Celery config validation failed: {error}")
        # Fallback to safe defaults
        broker_url = "redis://localhost:6379/0"
        backend_url = "redis://localhost:6379/1"

    app = Celery(
        "documind",
        broker=broker_url,
        backend=backend_url,
        include=["app.tasks.ingest_tasks"],
    )

    app.conf.update(
        # Queue configuration
        task_queues=QUEUES,
        task_default_queue="default",
        task_default_exchange="documind",
        task_default_routing_key="default",
        # Serialization
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_compression="gzip",
        # Time limits (configurable via settings)
        task_soft_time_limit=getattr(settings, "celery_task_soft_time_limit", 600),
        task_time_limit=getattr(settings, "celery_task_time_limit", 720),
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        broker_connection_timeout=30,
        broker_connection_retry_on_startup=True,
        broker_connection_max_retries=3,
        # Result backend
        result_expires=3600,
        result_extended=True,
        # Worker
        worker_prefetch_multiplier=1,
        worker_max_tasks_per_child=getattr(settings, "celery_max_tasks_per_child", 50),
        # Retry policy
        task_publish_retry=True,
        task_publish_retry_policy={
            "max_retries": 3,
            "interval_start": 0.2,
            "interval_step": 0.5,
            "interval_max": 2.0,
            "retry_for_exceptions": [
                "kombu.exceptions.OperationalError",
                "redis.exceptions.ConnectionError",
            ],
        },
        # Monitoring
        worker_send_task_events=True,
        task_send_sent_event=True,
        task_annotations={
            "*": {
                "on_failure": _on_task_failure,
            }
        },
    )

    return app


celery_app = create_celery_app()


# -- Worker lifecycle hooks -----------------------------------------------------


@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """Initialize resources when a Celery worker starts."""
    corr_id = "worker_startup"
    try:
        logger.info(f"[{corr_id}] Celery worker ready.")
        from app.core.logging_config import configure_logging

        configure_logging()
    except Exception as e:
        logger.error(f"[{corr_id}] Logging config failed: {e}", exc_info=True)
    except ImportError:
        # Logging module not available — continue without config
        logger.warning(f"[{corr_id}] Logging config module not found — using defaults")


@worker_shutdown.connect
def on_worker_shutdown(sender, **kwargs):
    corr_id = "worker_shutdown"
    try:
        logger.info(f"[{corr_id}] Celery worker shutting down.")
        # Optional: cleanup resources here
    except Exception as e:
        logger.error(f"[{corr_id}] Shutdown cleanup failed: {e}", exc_info=True)


def get_celery_metadata() -> dict[str, Any]:
    """✅ NEW: Return Celery metadata for monitoring."""
    settings = get_settings()
    return {
        "broker_url": getattr(settings, "celery_broker_url", "redis://localhost:6379/0"),
        "result_backend": getattr(settings, "celery_result_backend", "redis://localhost:6379/1"),
        "queues": [q.name for q in QUEUES],
        "queue_tier_map": QUEUE_TIER_MAP,
        "time_limits": {
            "soft": getattr(settings, "celery_task_soft_time_limit", 600),
            "hard": getattr(settings, "celery_task_time_limit", 720),
        },
        "retry_policy": {
            "max_retries": 3,
            "interval_start": 0.2,
            "interval_step": 0.5,
            "interval_max": 2.0,
        },
        "worker_settings": {
            "prefetch_multiplier": 1,
            "max_tasks_per_child": getattr(settings, "celery_max_tasks_per_child", 50),
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "celery_app",
    "QUEUES",
    "QUEUE_TIER_MAP",
    "create_celery_app",
    "get_celery_metadata",
]
# Local smoke test entry point. Run: python -m

