# backend/app/tasks/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Celery Tasks Module
Provides async task orchestration for:
- Document ingestion pipeline with progress tracking
- Priority-based queue routing
- Real-time WebSocket progress streaming
Public API:
from app.tasks import celery_app, ingest_document, ProgressPublisher
"""

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Celery App
    "celery_app",
    # Progress Tracking
    "ProgressPublisher",
    "ProgressSubscriber",
    "TaskStatus",
    "ProgressEvent",
    # Queue Config
    "get_queue_config",
    "QueueConfig",
    # Tasks
    "ingest_document",
    # Task Management
    "TaskManager",
    "get_task_manager",
    # Metadata helpers
    "get_tasks_metadata",
]

# ASCALE-S: Module metadata
__version__ = "1.1.0"
__description__ = "DocuMind AI Celery Task Orchestration"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Celery app
    "celery_app": (".celery_app", "celery_app"),
    # Progress tracking
    "ProgressPublisher": (".progress", "ProgressPublisher"),
    "ProgressSubscriber": (".progress", "ProgressSubscriber"),
    "TaskStatus": (".progress", "TaskStatus"),
    "ProgressEvent": (".progress", "ProgressEvent"),
    # Queue config
    "get_queue_config": (".priority", "get_queue_config"),
    "QueueConfig": (".priority", "QueueConfig"),
    # Tasks
    "ingest_document": (".ingest_tasks", "ingest_document"),
    # Task management
    "TaskManager": (".manager", "TaskManager"),
    "get_task_manager": (".manager", "get_task_manager"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Lazy imports to prevent circular dependencies.
    ✅ FIXED: Direct return + explicit error handling.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib

            module = importlib.import_module(module_path, package=__name__.rpartition(".")[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(f"Failed to lazy-import '{name}' from '{module_path}': {e}") from e

    if name == "get_tasks_metadata":
        from .celery_app import get_celery_metadata

        return get_celery_metadata

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


def _reset_caches_for_tests() -> None:
    """Reset internal caches for clean pytest runs."""
    import importlib
    import sys

    # Invalidate import caches
    for mod_name in [
        ".celery_app",
        ".ingest_tasks",
        ".progress",
        ".priority",
        ".manager",
    ]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass

    # ✅ FIXED: Reset module-level singletons
    try:
        from . import manager

        if hasattr(manager.TaskManager, "_tasks"):
            manager.TaskManager._tasks.clear()
    except ImportError:
        pass

    try:
        from . import celery_app

        if hasattr(celery_app, "tasks") and hasattr(celery_app.tasks, "registry"):
            celery_app.tasks.registry.clear()
    except ImportError:
        pass


# DVMELTSS-L: Module initialization logging for observability
__init_logged: bool = False


def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return

    import logging

    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Tasks module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_tasks_metadata() -> dict[str, Any]:
    """Return tasks module metadata for monitoring/debugging."""
    from .celery_app import get_celery_metadata
    from .priority import get_priority_metadata
    from .manager import get_task_metadata

    return {
        "version": __version__,
        "description": __description__,
        "celery": get_celery_metadata(),
        "priority": get_priority_metadata(),
        "manager": get_task_metadata(),
    }
