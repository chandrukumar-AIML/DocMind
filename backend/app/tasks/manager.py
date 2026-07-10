"""
Task management utilities for background job tracking.

NOTE: This is an in-memory store. In a multi-process Celery environment (default),
each worker has its own separate memory space. This manager is only consistent
if using a single-process worker (e.g., --pool=solo) or for local testing.
For production tracking across workers, use the Redis-backed `progress.py`.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from enum import Enum


logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """Check if status is a final state."""
        return status in {cls.COMPLETED, cls.FAILED, cls.CANCELLED}


class TaskManager:
    """
    In-memory task manager for tracking background jobs.

    ✅ FIXED: Thread-safe via RLock.
    ✅ FIXED: Input validation + robust date handling.
    """

    # Class-level state (Shared across instances in same process)
    _tasks: dict[str, dict[str, Any]] = {}

    # ✅ Thread safety lock
    _lock = threading.RLock()

    @classmethod
    def _validate_task_inputs(
        cls, task_type: str, workspace_id: str, corr_id: str = "task_manager"
    ) -> tuple[bool, str]:
        """Validate task inputs before processing."""
        if not isinstance(task_type, str) or not re.match(r"^[a-zA-Z0-9_]+$", task_type):
            return False, "task_type must be alphanumeric (with underscores)"
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return False, "workspace_id must be a non-empty string"
        return True, ""

    @classmethod
    def create_task(
        cls,
        task_type: str,
        workspace_id: str,
        correlation_id: Optional[str] = None,
        **metadata: Any,
    ) -> str:
        """Create a new task entry."""
        # Validate
        is_valid, error = cls._validate_task_inputs(task_type, workspace_id)
        if not is_valid:
            raise ValueError(error)

        task_id = str(uuid.uuid4())
        safe_corr_id = correlation_id if correlation_id else str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()

        task_data = {
            "task_id": task_id,
            "task_type": task_type,
            "workspace_id": workspace_id,
            "correlation_id": safe_corr_id,
            "status": TaskStatus.PENDING.value,
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "metadata": metadata,
        }

        # ✅ Thread-safe write
        with cls._lock:
            cls._tasks[task_id] = task_data

        logger.debug(f"Task created: {task_id} type={task_type} ws={workspace_id}")
        return task_id

    @classmethod
    def update_task(
        cls,
        task_id: str,
        status: Optional[TaskStatus | str] = None,
        result: Any = None,
        error: Optional[str] = None,
        **updates: Any,
    ) -> bool:
        """Update task status and metadata."""
        if not task_id:
            return False

        # ✅ Thread-safe update
        with cls._lock:
            if task_id not in cls._tasks:
                return False

            task = cls._tasks[task_id]

            # Validate status transition
            if status:
                status_val = status.value if isinstance(status, TaskStatus) else status
                current_status = task.get("status")

                # Prevent reopening completed tasks
                if TaskStatus.is_terminal(current_status) and status_val != current_status:
                    logger.warning(f"Attempt to update terminal task {task_id} from {current_status} to {status_val}")
                    return False

                task["status"] = status_val

            if result is not None:
                task["result"] = result
            if error:
                task["error"] = error

            task["updated_at"] = datetime.now(timezone.utc).isoformat()

            # Apply extra metadata updates safely
            for k, v in updates.items():
                if k not in ("task_id", "created_at"):  # Protect immutable fields
                    task[k] = v

        logger.debug(f"Task updated: {task_id} status={task.get('status')}")
        return True

    @classmethod
    def get_task(cls, task_id: str) -> Optional[dict]:
        """Get task details by ID."""
        with cls._lock:
            # Return a copy to prevent external mutation
            task = cls._tasks.get(task_id)
            return dict(task) if task else None

    @classmethod
    def list_tasks(
        cls,
        workspace_id: Optional[str] = None,
        status: Optional[TaskStatus | str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """List tasks with optional filtering."""
        status_val = status.value if isinstance(status, TaskStatus) else status

        with cls._lock:
            # Filter in-memory
            tasks = list(cls._tasks.values())

        if workspace_id:
            tasks = [t for t in tasks if t.get("workspace_id") == workspace_id]
        if status_val:
            tasks = [t for t in tasks if t.get("status") == status_val]

        # Sort by created_at descending (safe string sort for ISO format)
        tasks.sort(key=lambda t: t.get("created_at", ""), reverse=True)

        return tasks[offset : offset + limit]

    @classmethod
    def cancel_task(cls, task_id: str) -> bool:
        """Mark a task as cancelled."""
        return cls.update_task(task_id, status=TaskStatus.CANCELLED)

    @classmethod
    def cleanup_old_tasks(cls, max_age_hours: int = 24) -> int:
        """Remove tasks older than max_age_hours."""
        cutoff_ts = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        to_remove = []

        with cls._lock:
            for tid, task in cls._tasks.items():
                try:
                    created_at_str = task.get("created_at", "")
                    # Parse ISO format safely
                    if "T" in created_at_str:
                        ts = datetime.fromisoformat(created_at_str).timestamp()
                        if ts < cutoff_ts:
                            to_remove.append(tid)
                except (ValueError, TypeError):
                    # If date is invalid, remove it to prevent accumulation
                    to_remove.append(tid)

            for tid in to_remove:
                del cls._tasks[tid]

        if to_remove:
            logger.debug(f"Cleaned up {len(to_remove)} old tasks")
        return len(to_remove)

    @classmethod
    def get_stats(cls) -> dict[str, int]:
        """Return current task counts by status."""
        with cls._lock:
            stats = {"total": len(cls._tasks)}
            for task in cls._tasks.values():
                status = task.get("status", "unknown")
                stats[status] = stats.get(status, 0) + 1
            return stats


def get_task_manager() -> TaskManager:
    """Get the global task manager class (singleton pattern)."""
    return TaskManager


def get_task_metadata() -> dict[str, Any]:
    """✅ NEW: Return task manager metadata for monitoring."""
    return {
        "type": "in_memory",
        "thread_safe": True,
        "current_task_count": len(TaskManager._tasks),
        "warning": "In-memory storage is not shared across Celery worker processes.",
    }


# -- Module Exports -------------------------------------------------------

__all__ = ["TaskManager", "TaskStatus", "get_task_manager", "get_task_metadata"]
# Local smoke test entry point. Run: python -m

