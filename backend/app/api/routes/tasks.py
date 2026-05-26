# backend/app/api/routes/tasks.py
# DVMELTSS-FIX: M/E/S + ASCALE-A + WebSockets
# ✅ FIXED: Proper WebSocket auth + input validation + timeout handling + safe manager ops

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.models import ErrorResponse
from app.tasks.manager import TaskManager
from app.tasks.models import TaskStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])

# ✅ NEW: Operation timeouts (seconds)
_MANAGER_TIMEOUT: Final = 30.0
_WS_MAX_DURATION: Final = 600.0  # 10 minutes max WebSocket connection
_WS_POLL_INTERVAL: Final = 2.0


# ✅ NEW: Input validation helper
def _validate_task_inputs(
    task_id: Optional[str],
    limit: Optional[int],
    offset: Optional[int],
    status_filter: Optional[TaskStatus],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate task endpoint inputs before processing."""
    if task_id is not None and not isinstance(task_id, str):
        return False, "task_id must be a string or None"
    if limit is not None and (not isinstance(limit, int) or limit < 1 or limit > 100):
        return False, "limit must be between 1 and 100"
    if offset is not None and (not isinstance(offset, int) or offset < 0):
        return False, "offset must be >= 0"
    if status_filter is not None and not isinstance(status_filter, TaskStatus):
        return False, "status must be a TaskStatus enum or None"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/{task_id}",
    summary="Get async task status",
    description="Returns current status, progress, and result of a background task.",
)
async def get_task_status(
    task_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    corr_id = generate_correlation_id("task_status")
    
    # ✅ Validate inputs
    is_valid, error = _validate_task_inputs(task_id, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    manager = TaskManager()
    
    try:
        task = await asyncio.wait_for(
            asyncio.to_thread(manager.get_task, task_id),
            timeout=_MANAGER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Task status check timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Task status check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve task status")
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found or access denied")
    
    return {
        "task_id": getattr(task, "id", task_id),
        "status": getattr(task, "status", TaskStatus.UNKNOWN).value if hasattr(task, "status") else "unknown",
        "progress": getattr(task, "progress", 0),
        "message": getattr(task, "message", ""),
        "result": getattr(task, "result", None),
        "error": getattr(task, "error", None),
        "correlation_id": corr_id,
    }


@router.get(
    "",
    summary="List user's recent tasks",
    description="Paginated list of background tasks for the authenticated user.",
)
async def list_user_tasks(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: Optional[TaskStatus] = Query(default=None),
) -> dict:
    corr_id = generate_correlation_id("list_tasks")
    
    # ✅ Validate inputs
    is_valid, error = _validate_task_inputs(None, limit, offset, status, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    manager = TaskManager()
    
    try:
        tasks = await asyncio.wait_for(
            asyncio.to_thread(
                manager.list_tasks,
                workspace_id=user.workspace_id,
                status=status,
                limit=limit,
                offset=offset,
            ),
            timeout=_MANAGER_TIMEOUT,
        )
        
        return {
            "tasks": [t.model_dump() if hasattr(t, "model_dump") else {} for t in (tasks or [])],
            "total": len(tasks) if tasks else 0,
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] List tasks timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] List tasks failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list tasks")


@router.websocket("/ws/tasks/{task_id}")
async def websocket_task_progress(
    websocket: WebSocket,
    task_id: str,
    # ✅ FIXED: Use proper auth dependency for WebSocket
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    """
    WebSocket endpoint for real-time task progress updates.
    Client connects to /ws/tasks/{task_id} with valid JWT in query or header.
    """
    corr_id = generate_correlation_id("ws_task")
    
    # ✅ Validate inputs
    is_valid, error = _validate_task_inputs(task_id, None, None, None, corr_id)
    if not is_valid:
        await websocket.close(code=1008, reason=f"Invalid parameters: {error}")
        return
    
    await websocket.accept()
    
    manager = TaskManager()
    start_time = asyncio.get_running_loop().time()  # FIXED: get_event_loop() deprecated in Python 3.10+
    logger.info(f"[{corr_id}] WebSocket connected for task {task_id} user={user.user_id[:8]}...")
    
    try:
        while True:
            # ✅ Check max connection duration
            elapsed = asyncio.get_running_loop().time() - start_time  # FIXED: get_event_loop() deprecated in Python 3.10+
            if elapsed > _WS_MAX_DURATION:
                logger.warning(f"[{corr_id}] WebSocket connection exceeded max duration {_WS_MAX_DURATION}s")
                await websocket.send_json({"error": "Connection timeout"})
                break
            
            try:
                task = await asyncio.wait_for(
                    asyncio.to_thread(manager.get_task, task_id),
                    timeout=_MANAGER_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.warning(f"[{corr_id}] Task fetch timed out")
                await websocket.send_json({"error": "Task fetch timeout"})
                break
            except Exception as e:
                logger.error(f"[{corr_id}] Task fetch failed: {e}")
                await websocket.send_json({"error": "Internal error"})
                break
            
            if not task:
                await websocket.send_json({"error": "Task not found or access denied"})
                break
            
            # Send progress update
            await websocket.send_json({
                "type": "progress",
                "task_id": getattr(task, "id", task_id),
                "status": getattr(task, "status", TaskStatus.UNKNOWN).value if hasattr(task, "status") else "unknown",
                "progress": getattr(task, "progress", 0),
                "message": getattr(task, "message", ""),
                "correlation_id": corr_id,
            })
            
            # If task is terminal, close connection
            task_status = getattr(task, "status", None)
            if task_status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                await websocket.send_json({
                    "type": "final",
                    "task_id": getattr(task, "id", task_id),
                    "status": task_status.value if hasattr(task_status, "value") else str(task_status),
                    "result": getattr(task, "result", None),
                    "error": getattr(task, "error", None),
                    "correlation_id": corr_id,
                })
                break
            
            # Poll interval
            await asyncio.sleep(_WS_POLL_INTERVAL)
            
    except WebSocketDisconnect:
        logger.info(f"[{corr_id}] WebSocket disconnected for task {task_id}")
    except Exception as e:
        logger.error(f"[{corr_id}] WebSocket error: {e}")
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            pass


@router.post(
    "/{task_id}/cancel",
    status_code=200,
    summary="Cancel a running task",
)
async def cancel_task(
    task_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    corr_id = generate_correlation_id("cancel_task")
    
    # ✅ Validate inputs
    is_valid, error = _validate_task_inputs(task_id, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    manager = TaskManager()
    
    try:
        # ✅ FIXED: Verify task ownership before cancellation
        task = await asyncio.wait_for(
            asyncio.to_thread(manager.get_task, task_id),
            timeout=_MANAGER_TIMEOUT,
        )
        
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found or access denied")
        
        # Check if task belongs to user (additional safety)
        if hasattr(task, "user_id") and task.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="Cannot cancel task owned by another user")
        
        success = await asyncio.wait_for(
            asyncio.to_thread(manager.cancel_task, task_id),
            timeout=_MANAGER_TIMEOUT,
        )
        
        if not success:
            task_status = getattr(task, "status", None)
            if task_status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
                raise HTTPException(status_code=400, detail="Task is already in a terminal state")
            raise HTTPException(status_code=400, detail="Task cannot be cancelled")
        
        return {
            "status": "cancelled",
            "task_id": task_id,
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Cancel task timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Cancel task failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to cancel task")


def get_tasks_metadata() -> dict[str, Any]:
    """✅ NEW: Return tasks API metadata for monitoring."""
    return {
        "endpoints": [
            "/tasks/{task_id}",
            "/tasks",
            "/tasks/ws/tasks/{task_id}",
            "/tasks/{task_id}/cancel",
        ],
        "timeouts": {
            "manager_seconds": _MANAGER_TIMEOUT,
            "websocket_max_duration_seconds": _WS_MAX_DURATION,
            "websocket_poll_interval_seconds": _WS_POLL_INTERVAL,
        },
        "limits": {
            "list_limit_min": 1,
            "list_limit_max": 100,
            "list_offset_min": 0,
        },
        "supported_statuses": [s.value for s in TaskStatus] if TaskStatus else [],
        "websocket_auth_required": True,
        "workspace_scoped": False,  # Tasks are user-scoped
    }


__all__ = ["router", "get_tasks_metadata"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

