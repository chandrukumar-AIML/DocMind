
from __future__ import annotations

import asyncio
import datetime
import logging
import re
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_workspace_admin, AuthenticatedUser
from app.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspaces", tags=["workspaces"])

_MANAGER_TIMEOUT: Final = 30.0
_WORKSPACE_ID_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")


# ========================================================================
# PYDANTIC MODELS
# ========================================================================
class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=3, max_length=64)
    description: str = Field(default="", max_length=500)


class WorkspaceResponse(BaseModel):
    workspace_id: str
    name: str
    description: str
    created_at: str
    owner_id: str


def _validate_workspace_inputs(
    name: Optional[str],
    description: Optional[str],
    workspace_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate workspace endpoint inputs before processing."""
    if name is not None and (not isinstance(name, str) or len(name.strip()) < 3 or len(name.strip()) > 64):
        return False, "name must be a string between 3 and 64 characters"
    if description is not None and not isinstance(description, str):
        return False, "description must be a string or None"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace",
    description="Creates an isolated workspace for document indexing. Admin or allowed users only.",
)
async def create_workspace(
    request: WorkspaceCreateRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> WorkspaceResponse:
    corr_id = generate_correlation_id("create_workspace")

    # ✅ Validate inputs
    is_valid, error = _validate_workspace_inputs(request.name, request.description, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    workspace_id = request.name.strip().lower().replace(" ", "_")
    # Remove any non-alphanumeric/underscore chars
    workspace_id = re.sub(r"[^a-z0-9_]", "", workspace_id)

    if not workspace_id or not _WORKSPACE_ID_PATTERN.match(workspace_id):
        raise HTTPException(
            status_code=400,
            detail="Workspace name must contain only letters, numbers, and underscores",
        )

    manager = WorkspaceManager()

    try:
        # Check if exists with timeout
        exists = await asyncio.wait_for(
            manager.workspace_exists_async(workspace_id),
            timeout=_MANAGER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Workspace check timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Workspace check failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to check workspace existence")

    if exists:
        raise HTTPException(status_code=409, detail="Workspace ID already exists")

    try:
        # Create workspace with timeout
        await asyncio.wait_for(
            manager.create_workspace_async(
                workspace_id=workspace_id,
                owner_id=user.user_id,
                description=request.description,
                correlation_id=corr_id,
            ),
            timeout=_MANAGER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Workspace creation timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Workspace creation timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Workspace creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workspace")

    logger.info(f"[{corr_id}] Workspace created: {workspace_id} by {user.user_id}")

    return WorkspaceResponse(
        workspace_id=workspace_id,
        name=request.name,
        description=request.description,
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        owner_id=user.user_id,
    )


@router.get(
    "",
    summary="List workspaces accessible by the user",
    description="Returns all workspaces the user owns or has access to.",
)
async def list_workspaces(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> list[WorkspaceResponse]:
    corr_id = generate_correlation_id("list_workspaces")

    manager = WorkspaceManager()

    try:
        workspaces = await asyncio.wait_for(
            manager.list_user_workspaces(user.user_id),
            timeout=_MANAGER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] List workspaces timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] List workspaces failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list workspaces")

    result = []
    for ws in workspaces or []:
        try:
            result.append(
                WorkspaceResponse(
                    workspace_id=getattr(ws, "workspace_id", ""),
                    name=getattr(ws, "name", ""),
                    description=getattr(ws, "description", ""),
                    created_at=getattr(ws, "created_at", ""),
                    owner_id=getattr(ws, "owner_id", ""),
                )
            )
        except Exception as e:
            logger.warning(f"[{corr_id}] Failed to serialize workspace: {e}")
            continue

    return result


@router.get(
    "/{workspace_id}",
    summary="Get workspace details",
)
async def get_workspace(
    workspace_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> WorkspaceResponse:
    corr_id = generate_correlation_id("get_workspace")

    # ✅ Validate inputs
    is_valid, error = _validate_workspace_inputs(None, None, workspace_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    manager = WorkspaceManager()

    try:
        ws = await asyncio.wait_for(
            manager.get_workspace_async(workspace_id),
            timeout=_MANAGER_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Get workspace timed out after {_MANAGER_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Get workspace failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve workspace")

    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    ws_owner_id = getattr(ws, "owner_id", None)
    user_role = getattr(user, "role", "")

    if ws_owner_id != user.user_id and user_role != "admin":
        raise HTTPException(status_code=403, detail="Access denied to this workspace")

    return WorkspaceResponse(
        workspace_id=getattr(ws, "workspace_id", workspace_id),
        name=getattr(ws, "name", ""),
        description=getattr(ws, "description", ""),
        created_at=getattr(ws, "created_at", ""),
        owner_id=ws_owner_id or "",
    )


def get_workspace_api_metadata() -> dict[str, Any]:
    """✅ NEW: Return workspace API metadata for monitoring."""
    return {
        "endpoints": [
            "/workspaces",
            "/workspaces/{workspace_id}",
        ],
        "timeout_seconds": _MANAGER_TIMEOUT,
        "workspace_id_pattern": _WORKSPACE_ID_PATTERN.pattern,
        "name_length_min": 3,
        "name_length_max": 64,
        "admin_required_for_create": False,  # Can be configured
        "workspace_isolation": True,
    }



# ── Budget / Cost Governance endpoints ──────────────────────────────────────

@router.get(
    "/{workspace_id}/budget",
    summary="Get workspace LLM budget and monthly usage",
    tags=["budget"],
)
async def get_workspace_budget(
    workspace_id: str,
    user: Annotated[AuthenticatedUser, Depends(require_workspace_admin)],
):
    from app.core.cost_governor import get_cost_governor
    governor = get_cost_governor()
    return await governor.get_monthly_summary(workspace_id)


@router.put(
    "/{workspace_id}/budget",
    summary="Set monthly token budget for workspace",
    tags=["budget"],
)
async def set_workspace_budget(
    workspace_id: str,
    monthly_tokens: int,
    user: Annotated[AuthenticatedUser, Depends(require_workspace_admin)],
):
    from app.core.cost_governor import get_cost_governor
    governor = get_cost_governor()
    ok = await governor.set_budget(workspace_id, monthly_tokens)
    return {"workspace_id": workspace_id, "monthly_token_budget": monthly_tokens, "saved": ok}


__all__ = ["router", "get_workspace_api_metadata"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.api.routes.workspace) -
# ========================================================================

