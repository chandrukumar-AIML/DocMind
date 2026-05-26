# backend/app/api/routes/audit.py
"""Audit log API — filterable, exportable, role-gated."""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin, require_superadmin
from app.core.audit_logger import query_audit_log, export_audit_csv

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit")


@router.get("/logs")
async def get_audit_logs(
    workspace_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    from_dt: Optional[datetime] = Query(None),
    to_dt: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    """
    workspace_admin: can only query their own workspace.
    superadmin: can query any workspace (or all if workspace_id omitted).
    """
    if user.is_superuser:
        ws_id = workspace_id  # None = all workspaces
    else:
        ws_id = user.workspace_id  # force own workspace

    rows = await query_audit_log(
        workspace_id=ws_id,
        action=action,
        severity=severity,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
        offset=offset,
    )
    return {"logs": rows, "count": len(rows), "workspace_id": ws_id}


@router.get("/export/{workspace_id}")
async def export_audit(
    workspace_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    """Download full audit log as CSV."""
    if not user.is_superuser and workspace_id != user.workspace_id:
        raise HTTPException(status_code=403, detail="Access denied")

    csv_data = await export_audit_csv(workspace_id)
    return StreamingResponse(
        io.StringIO(csv_data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_{workspace_id}.csv"'},
    )


if __name__ == "__main__":
    print("Audit routes:", [r.path for r in router.routes])
