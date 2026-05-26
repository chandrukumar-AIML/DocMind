"""Task-related Pydantic models for FastAPI validation."""
from __future__ import annotations

import re
import uuid
import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict

# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import CorrelationIdField, sanitize_text


class TaskStatus(str, Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCreateRequest(BaseModel):
    """Request to create a new background task."""
    task_type: str = Field(..., min_length=1, max_length=64, description="Type of task to create")
    workspace_id: Optional[str] = Field(default=None, max_length=64, description="Workspace context")
    priority: str = Field(default="default", pattern="^(high|default|bulk)$", description="Task priority tier")
    metadata: Optional[dict[str, Any]] = Field(default=None, description="Additional task metadata")
    correlation_id: Optional[str] = CorrelationIdField

    # ✅ FIXED: Pydantic v2 config
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_type": "ingest_document",
                "workspace_id": "ws_default",
                "priority": "default",
                "metadata": {"file_size_mb": 15.5},
                "correlation_id": "req-xyz789",
            }
        }
    )

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, v: str) -> str:
        """Ensure task type is alphanumeric with underscores only."""
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("task_type may contain only letters, numbers, and underscores")
        return v
    
    @model_validator(mode="before")
    @classmethod
    def sanitize_metadata(cls, data: Any) -> Any:
        """Sanitize metadata dict to ensure JSON-serializability."""
        # ✅ FIXED: Proper signature with cls + data parameters
        if not isinstance(data, dict):
            return data
            
        if data.get("metadata"):
            safe_meta = {}
            for k, v in data["metadata"].items():
                # Keep only JSON-serializable types
                if isinstance(v, (str, int, float, bool, type(None))):
                    safe_meta[k] = v
                elif isinstance(v, (list, dict)):
                    try:
                        json.dumps(v)
                        safe_meta[k] = v
                    except (TypeError, ValueError):
                        safe_meta[k] = str(v)
                # Skip functions, classes, etc.
            data["metadata"] = safe_meta
        return data


class TaskResponse(BaseModel):
    """Task details returned to API clients."""
    task_id: str
    task_type: str
    workspace_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None
    correlation_id: Optional[str] = None

    # ✅ FIXED: Pydantic v2 config with from_attributes
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "task_id": "task_abc123",
                "task_type": "ingest_document",
                "workspace_id": "ws_default",
                "status": "completed",
                "created_at": "2026-04-28T12:00:00Z",
                "updated_at": "2026-04-28T12:05:00Z",
                "result": {"chunks": 45},
                "correlation_id": "req-xyz789",
            }
        }
    )
    
    # ✅ NEW: Helper to convert from dict (for ORM compatibility)
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskResponse":
        """Create TaskResponse from dict with safe field mapping."""
        # ✅ FIXED: Proper classmethod signature with cls parameter
        
        def _safe_datetime(value: Any) -> Optional[datetime]:
            """Safely parse datetime from string or return as-is."""
            if value is None:
                return None
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                try:
                    # Handle ISO format with or without timezone
                    if value.endswith("Z"):
                        value = value[:-1] + "+00:00"
                    return datetime.fromisoformat(value)
                except ValueError:
                    return None
            return None
        
        return cls(
            task_id=data.get("task_id", ""),
            task_type=data.get("task_type", ""),
            workspace_id=data.get("workspace_id", ""),
            status=TaskStatus(data.get("status", "pending")) if data.get("status") else TaskStatus.PENDING,
            created_at=_safe_datetime(data.get("created_at")) or datetime.now(),
            updated_at=_safe_datetime(data.get("updated_at")),
            result=data.get("result"),
            error=data.get("error"),
            metadata=data.get("metadata"),
            correlation_id=data.get("correlation_id"),
        )


class TaskListResponse(BaseModel):
    """Paginated list of tasks."""
    tasks: list[TaskResponse]
    total: int = Field(..., ge=0, description="Total number of tasks")
    limit: int = Field(..., ge=1, le=1000, description="Number of tasks per page")
    offset: int = Field(..., ge=0, description="Offset for pagination")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tasks": [],
                "total": 0,
                "limit": 50,
                "offset": 0,
            }
        }
    )


class TaskCancelRequest(BaseModel):
    """Request to cancel a running task."""
    task_id: str = Field(..., description="ID of task to cancel")
    reason: Optional[str] = Field(default=None, max_length=255, description="Optional cancellation reason")
    
    @field_validator("task_id")
    @classmethod
    def validate_task_id_format(cls, v: str) -> str:
        """Ensure task_id is a valid UUID format."""
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("task_id must be a valid UUID")
        return v


def get_task_models_metadata() -> dict[str, Any]:
    """✅ NEW: Return task models metadata for monitoring."""
    return {
        "models": [
            "TaskStatus",
            "TaskCreateRequest",
            "TaskResponse",
            "TaskListResponse",
            "TaskCancelRequest",
        ],
        "pydantic_version": "2.x",
        "validation_features": [
            "field_validator",
            "model_validator",
            "ConfigDict",
        ],
    }


# -- Module Exports ---------------------------------------------------------
__all__ = [
    "TaskStatus",
    "TaskCreateRequest",
    "TaskResponse",
    "TaskListResponse",
    "TaskCancelRequest",
    "get_task_models_metadata",
]  # ✅ FIXED: Closed the list properly
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

