"""Team Collaboration — Document Status & Assignment (Feature #7).

Lightweight review workflow: each document in a workspace can have a
status (pending_review | reviewed | filed | flagged) and an optional
assignee (free-text for now — email or name of a team member).
Stored in a simple `document_status` table.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.database.session import get_async_db
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc-status", tags=["doc-status"])

_VALID_STATUSES = {"pending_review", "reviewed", "filed", "flagged", "none"}

# ── Ensure table exists ───────────────────────────────────────────────────────

_table_ready = False

async def _ensure_table(db: AsyncSession):
    global _table_ready
    if _table_ready:
        return
    # asyncpg requires separate execute per statement
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS document_status (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL,
            document_id  TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'none',
            assignee     TEXT,
            note         TEXT,
            updated_by   TEXT,
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_doc_status_workspace UNIQUE (workspace_id, document_id)
        )
    """))
    await db.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_doc_status_workspace ON document_status (workspace_id)"
    ))
    await db.commit()
    _table_ready = True


# ── Schemas ───────────────────────────────────────────────────────────────────

class DocStatusUpdate(BaseModel):
    document_id:  str  = Field(..., max_length=512)
    status:       str  = Field(default="none")
    assignee:     Optional[str] = Field(default=None, max_length=128)
    note:         Optional[str] = Field(default=None, max_length=500)
    workspace_id: Optional[str] = None


class DocStatusItem(BaseModel):
    document_id: str
    status:      str
    assignee:    Optional[str]
    note:        Optional[str]
    updated_by:  Optional[str]
    updated_at:  Optional[str]


class DocStatusMap(BaseModel):
    statuses: dict[str, DocStatusItem]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/map", response_model=DocStatusMap)
async def get_status_map(
    workspace_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return {document_id: DocStatusItem} for all docs in the workspace."""
    await _ensure_table(db)
    wsid = workspace_id or user.workspace_id
    rows = (await db.execute(
        text("SELECT document_id, status, assignee, note, updated_by, updated_at "
             "FROM document_status WHERE workspace_id = :w"),
        {"w": wsid},
    )).fetchall()
    result = {}
    for r in rows:
        result[r.document_id] = DocStatusItem(
            document_id=r.document_id,
            status=r.status,
            assignee=r.assignee,
            note=r.note,
            updated_by=r.updated_by,
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )
    return DocStatusMap(statuses=result)


@router.post("/update", response_model=DocStatusItem)
async def update_doc_status(
    req: DocStatusUpdate,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Upsert the status / assignee for a document."""
    await _ensure_table(db)
    if req.status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status '{req.status}'. Use: {sorted(_VALID_STATUSES)}")
    wsid = req.workspace_id or user.workspace_id
    now  = datetime.now(timezone.utc)

    await db.execute(text("""
        INSERT INTO document_status (workspace_id, document_id, status, assignee, note, updated_by, updated_at)
        VALUES (:w, :d, :s, :a, :n, :u, :t)
        ON CONFLICT (workspace_id, document_id)
        DO UPDATE SET status=EXCLUDED.status, assignee=EXCLUDED.assignee,
                      note=EXCLUDED.note, updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at
    """), {"w": wsid, "d": req.document_id, "s": req.status,
           "a": req.assignee, "n": req.note, "u": user.email, "t": now})
    await db.commit()

    return DocStatusItem(
        document_id=req.document_id,
        status=req.status,
        assignee=req.assignee,
        note=req.note,
        updated_by=user.email,
        updated_at=now.isoformat(),
    )
