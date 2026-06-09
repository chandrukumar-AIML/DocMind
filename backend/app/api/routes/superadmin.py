# backend/app/api/routes/superadmin.py
"""Superadmin API — all routes require is_superuser=True."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
import io

from app.auth.dependencies import AuthenticatedUser, require_superadmin
from app.core import superadmin_utils as sa

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/superadmin")


def _guard(user: AuthenticatedUser) -> AuthenticatedUser:
    """Inline superadmin guard — raises 403 if not superuser."""
    if not user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin access required.")
    return user


# ── Overview ──────────────────────────────────────────────────────────────────


@router.get("/overview")
async def overview(user: AuthenticatedUser = Depends(require_superadmin)):
    """Platform-wide stats cards."""
    stats = await sa.get_system_stats()
    top = await sa.get_top_workspaces(5)
    return {**stats, "top_workspaces_by_usage": top}


# ── Workspace management ──────────────────────────────────────────────────────


@router.get("/workspaces")
async def list_workspaces(
    search: Optional[str] = Query(None),
    plan: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_superadmin),
):
    return {"workspaces": await sa.list_all_workspaces(search, plan, is_active, limit, offset)}


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    ws = await sa.get_workspace_detail(workspace_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


class WorkspaceCreateRequest(BaseModel):
    client_name: str = Field(..., min_length=2, max_length=128)
    client_email: EmailStr
    plan: str = Field("starter", pattern="^(starter|business|enterprise)$")
    domain_type: Optional[str] = None
    max_docs: int = Field(100, ge=1)
    max_queries_per_day: int = Field(500, ge=1)
    max_storage_gb: float = Field(5.0, ge=0.1)
    send_invite: bool = True


@router.post("/workspace/create", status_code=201)
async def create_workspace(
    body: WorkspaceCreateRequest,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    ws = await sa.create_workspace_for_client(
        client_name=body.client_name,
        client_email=body.client_email,
        plan=body.plan,
        domain_type=body.domain_type,
        max_docs=body.max_docs,
        max_queries_per_day=body.max_queries_per_day,
        max_storage_gb=body.max_storage_gb,
    )

    invite_token = None
    if body.send_invite:
        try:
            from app.core.invite_manager import create_invite, send_invite_email

            raw_token, inv = await create_invite(
                email=body.client_email,
                workspace_id=ws["id"],
                role="workspace_admin",
                invited_by_user_id=user.user_id,
            )
            await send_invite_email(
                to_email=body.client_email,
                workspace_name=body.client_name,
                inviter_name="DocuMind Admin",
                raw_token=raw_token,
                role="workspace_admin",
            )
            invite_token = raw_token[:8] + "…"
        except Exception as e:
            logger.warning(f"Invite email failed (workspace still created): {e}")

    return {**ws, "invite_sent": body.send_invite, "invite_prefix": invite_token}


class WorkspaceLimitsRequest(BaseModel):
    max_docs: Optional[int] = Field(None, ge=1)
    max_queries_per_day: Optional[int] = Field(None, ge=1)
    max_storage_gb: Optional[float] = Field(None, ge=0.1)
    plan: Optional[str] = Field(None, pattern="^(starter|business|enterprise)$")


@router.put("/workspace/{workspace_id}/limits")
async def update_limits(
    workspace_id: str,
    body: WorkspaceLimitsRequest,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    result = await sa.update_workspace_limits(
        workspace_id,
        body.max_docs,
        body.max_queries_per_day,
        body.max_storage_gb,
        body.plan,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return result


class SuspendRequest(BaseModel):
    reason: str = Field("Suspended by admin", min_length=1, max_length=500)


@router.put("/workspace/{workspace_id}/suspend")
async def suspend_workspace(
    workspace_id: str,
    body: SuspendRequest,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    await sa.suspend_workspace(workspace_id, body.reason)
    return {"workspace_id": workspace_id, "suspended": True, "reason": body.reason}


@router.put("/workspace/{workspace_id}/reactivate")
async def reactivate_workspace(
    workspace_id: str,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    await sa.activate_workspace(workspace_id)
    return {"workspace_id": workspace_id, "active": True}


# ── Impersonation ─────────────────────────────────────────────────────────────


@router.post("/workspace/{workspace_id}/impersonate")
async def impersonate(
    workspace_id: str,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    """
    Generate a 1-hour JWT scoped to the workspace_admin of the given workspace.
    Logged to audit trail automatically.
    """
    try:
        result = await sa.create_impersonation_token(user.user_id, workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return result


# ── Audit log ─────────────────────────────────────────────────────────────────


@router.get("/workspace/{workspace_id}/audit-log")
async def audit_log(
    workspace_id: str,
    limit: int = Query(1000, ge=1, le=5000),
    action: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None),
    to_dt: Optional[datetime] = Query(None),
    user: AuthenticatedUser = Depends(require_superadmin),
):
    rows = await sa.get_workspace_audit_log(workspace_id, limit, action, from_dt, to_dt)
    return {"workspace_id": workspace_id, "count": len(rows), "logs": rows}


# ── Billing ───────────────────────────────────────────────────────────────────


@router.get("/workspace/{workspace_id}/billing")
async def workspace_billing(
    workspace_id: str,
    user: AuthenticatedUser = Depends(require_superadmin),
):
    b = await sa.get_workspace_billing(workspace_id)
    if not b:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return b


@router.get("/billing/export")
async def billing_export(
    month: Optional[str] = Query(None, description="YYYY-MM format"),
    user: AuthenticatedUser = Depends(require_superadmin),
):
    """Download billing CSV for all workspaces."""
    csv_data = await sa.export_billing_csv(month)
    filename = f"billing_{month or 'current'}.csv"
    return StreamingResponse(
        io.StringIO(csv_data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── System ────────────────────────────────────────────────────────────────────


@router.get("/system/health")
async def system_health(user: AuthenticatedUser = Depends(require_superadmin)):
    return await sa.get_system_health()


@router.get("/system/tasks")
async def celery_tasks(user: AuthenticatedUser = Depends(require_superadmin)):
    return await sa.get_celery_stats()


@router.post("/system/flush-cache")
async def flush_cache(user: AuthenticatedUser = Depends(require_superadmin)):
    return await sa.flush_redis_cache()


# ── Stats (legacy alias) ──────────────────────────────────────────────────────


@router.get("/stats")
async def stats_alias(user: AuthenticatedUser = Depends(require_superadmin)):
    return await sa.get_system_stats()


if __name__ == "__main__":
    print("Superadmin routes:", [r.path for r in router.routes])
