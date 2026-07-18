"""Audit Trail per Client File — Feature #15 (also serves #12).

Every significant action on a document (upload, query, status change,
draft reply, discrepancy scan, export) gets logged to an audit_events table.
CAs can download a full trail per client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.database.session import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["audit"])

_table_ready = False

async def _ensure(db: AsyncSession):
    global _table_ready
    if _table_ready: return
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id           BIGSERIAL PRIMARY KEY,
            workspace_id UUID NOT NULL,
            document_id  TEXT,
            client_id    UUID,
            actor_email  TEXT NOT NULL,
            action       TEXT NOT NULL,
            detail       TEXT,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_audit_workspace ON audit_events (workspace_id, created_at DESC)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_audit_client ON audit_events (client_id, created_at DESC)"
    ))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_audit_document ON audit_events (document_id, created_at DESC)"
    ))
    await db.commit()
    _table_ready = True


# ── Schemas ───────────────────────────────────────────────────────────────────

class AuditEvent(BaseModel):
    id:          Optional[int] = None
    document_id: Optional[str] = None
    client_id:   Optional[str] = None
    actor_email: str
    action:      str
    detail:      Optional[str] = None
    created_at:  Optional[str] = None


class LogRequest(BaseModel):
    action:      str  = Field(..., max_length=64)
    document_id: Optional[str] = Field(default=None, max_length=512)
    client_id:   Optional[str] = None
    detail:      Optional[str] = Field(default=None, max_length=500)
    workspace_id: Optional[str] = None


class AuditResponse(BaseModel):
    events: list[AuditEvent]
    total:  int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/log", response_model=AuditEvent)
async def log_event(
    req: LogRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_async_db),
):
    """Log an audit event (called by frontend after key actions)."""
    await _ensure(db)
    wsid = req.workspace_id or user.workspace_id
    now  = datetime.now(timezone.utc)
    row = await db.execute(text("""
        INSERT INTO audit_events (workspace_id, document_id, client_id, actor_email, action, detail, created_at)
        VALUES (:w, :d, :c, :a, :act, :det, :t)
        RETURNING id, created_at
    """), {"w": wsid, "d": req.document_id, "c": req.client_id,
           "a": user.email, "act": req.action, "det": req.detail, "t": now})
    r = row.fetchone()
    await db.commit()
    return AuditEvent(id=r.id, document_id=req.document_id, client_id=req.client_id,
                      actor_email=user.email, action=req.action, detail=req.detail,
                      created_at=r.created_at.isoformat())


@router.get("/list", response_model=AuditResponse)
async def list_events(
    workspace_id: Optional[str] = None,
    document_id:  Optional[str] = None,
    client_id:    Optional[str] = None,
    limit: int = Query(default=50, le=200),
    user: AuthenticatedUser = Depends(get_current_user),
    db:   AsyncSession = Depends(get_async_db),
):
    """Retrieve audit events filtered by document or client."""
    await _ensure(db)
    wsid = workspace_id or user.workspace_id
    filters = ["workspace_id = :w"]
    params: dict = {"w": wsid, "lim": limit}
    if document_id:
        filters.append("document_id = :d"); params["d"] = document_id
    if client_id:
        filters.append("client_id = :c"); params["c"] = client_id
    where = " AND ".join(filters)
    rows = (await db.execute(
        text(f"SELECT id, document_id, client_id, actor_email, action, detail, created_at "
             f"FROM audit_events WHERE {where} ORDER BY created_at DESC LIMIT :lim"),
        params,
    )).fetchall()
    events = [AuditEvent(id=r.id, document_id=r.document_id, client_id=str(r.client_id) if r.client_id else None,
                         actor_email=r.actor_email, action=r.action, detail=r.detail,
                         created_at=r.created_at.isoformat()) for r in rows]
    return AuditResponse(events=events, total=len(events))
