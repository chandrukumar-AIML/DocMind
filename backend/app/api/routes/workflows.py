# backend/app/api/routes/workflows.py
"""Workflow automation API: create, list, update, delete, and view run history."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, require_admin, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.workflow_engine import evaluate_conditions
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workflows", tags=["workflows"])

_VALID_TRIGGERS = {
    "document_ingested", "query_answered", "extraction_complete",
    "alert_triggered", "manual",
}
_VALID_ACTION_TYPES = {"webhook", "email", "tag", "domain_analysis"}


# ── Pydantic models ────────────────────────────────────────────

class WorkflowCondition(BaseModel):
    field: str = Field(..., max_length=64)
    operator: str = Field(..., pattern="^(eq|neq|gt|lt|gte|lte|contains|not_contains|in|regex)$")
    value: Any


class WorkflowAction(BaseModel):
    type: str = Field(..., pattern="^(webhook|email|tag|domain_analysis)$")
    recipient: Optional[str] = Field(default=None, max_length=256)
    subject: Optional[str] = Field(default=None, max_length=256)
    body_template: Optional[str] = Field(default=None, max_length=2000)
    tag_value: Optional[str] = Field(default=None, max_length=64)
    domain: Optional[str] = Field(default=None, max_length=64)
    webhook_url: Optional[str] = Field(default=None, max_length=2048)


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    trigger_event: str = Field(...)
    conditions: list[WorkflowCondition] = Field(default_factory=list)
    actions: list[WorkflowAction] = Field(..., min_length=1)
    is_active: bool = True


class WorkflowUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, max_length=128)
    description: Optional[str] = Field(default=None, max_length=500)
    conditions: Optional[list[WorkflowCondition]] = None
    actions: Optional[list[WorkflowAction]] = None
    is_active: Optional[bool] = None


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    req: WorkflowCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-create")

    if req.trigger_event not in _VALID_TRIGGERS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid trigger. Valid: {_VALID_TRIGGERS}"
        )

    wf_id = str(uuid.uuid4())
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO workflows
                    (id, workspace_id, name, description, trigger_event,
                     conditions, actions, is_active, created_by)
                VALUES
                    (:id, :ws, :name, :desc, :trigger,
                     CAST(:conditions AS jsonb), CAST(:actions AS jsonb), :active, :by)
            """), {
                "id": wf_id,
                "ws": user.workspace_id,
                "name": req.name,
                "desc": req.description,
                "trigger": req.trigger_event,
                "conditions": json.dumps([c.model_dump() for c in req.conditions]),
                "actions": json.dumps([a.model_dump() for a in req.actions]),
                "active": req.is_active,
                "by": user.user_id,
            })
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to create workflow: {e}")
        raise HTTPException(status_code=500, detail="Failed to create workflow")

    return {"workflow_id": wf_id, "name": req.name, "correlation_id": corr_id}


@router.get("/list")
async def list_workflows(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-list")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(text("""
                SELECT id, name, description, trigger_event, is_active,
                       created_at, updated_at,
                       jsonb_array_length(conditions) as cond_count,
                       jsonb_array_length(actions) as action_count
                FROM workflows
                WHERE workspace_id = :ws
                ORDER BY created_at DESC
            """), {"ws": user.workspace_id})
            wfs = rows.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list workflows: {e}")

    return {
        "workflows": [
            {
                "workflow_id": str(w[0]),
                "name": w[1],
                "description": w[2],
                "trigger_event": w[3],
                "is_active": w[4],
                "created_at": w[5].isoformat() if w[5] else None,
                "updated_at": w[6].isoformat() if w[6] else None,
                "condition_count": w[7] or 0,
                "action_count": w[8] or 0,
            }
            for w in wfs
        ],
        "total": len(wfs),
        "correlation_id": corr_id,
    }


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-get")
    async with async_engine.begin() as conn:
        row = await conn.execute(text("""
            SELECT id, name, description, trigger_event, conditions, actions,
                   is_active, created_by, created_at
            FROM workflows
            WHERE id = :id AND workspace_id = :ws
        """), {"id": workflow_id, "ws": user.workspace_id})
        wf = row.fetchone()

    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return {
        "workflow_id": str(wf[0]),
        "name": wf[1],
        "description": wf[2],
        "trigger_event": wf[3],
        "conditions": wf[4] if isinstance(wf[4], list) else json.loads(wf[4] or "[]"),
        "actions": wf[5] if isinstance(wf[5], list) else json.loads(wf[5] or "[]"),
        "is_active": wf[6],
        "created_by": wf[7],
        "created_at": wf[8].isoformat() if wf[8] else None,
        "correlation_id": corr_id,
    }


@router.patch("/{workflow_id}")
async def update_workflow(
    workflow_id: str,
    req: WorkflowUpdateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-update")
    updates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.description is not None:
        updates["description"] = req.description
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.conditions is not None:
        updates["conditions"] = json.dumps([c.model_dump() for c in req.conditions])
    if req.actions is not None:
        updates["actions"] = json.dumps([a.model_dump() for a in req.actions])

    if not updates:
        raise HTTPException(status_code=422, detail="No fields to update")

    set_clause = ", ".join(
        f"{k} = :{k}{'::jsonb' if k in ('conditions','actions') else ''}"
        for k in updates
    )
    updates["id"] = workflow_id
    updates["ws"] = user.workspace_id

    try:
        async with async_engine.begin() as conn:
            result = await conn.execute(
                text(f"UPDATE workflows SET {set_clause}, updated_at = NOW() WHERE id = :id AND workspace_id = :ws"),
                updates,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Workflow not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update: {e}")

    return {"updated": True, "workflow_id": workflow_id, "correlation_id": corr_id}


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-del")
    async with async_engine.begin() as conn:
        result = await conn.execute(text("""
            UPDATE workflows SET is_active = FALSE
            WHERE id = :id AND workspace_id = :ws
        """), {"id": workflow_id, "ws": user.workspace_id})
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Workflow not found")
    return {"deleted": True, "workflow_id": workflow_id, "correlation_id": corr_id}


@router.get("/{workflow_id}/runs")
async def get_workflow_runs(
    workflow_id: str,
    limit: int = 20,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wf-runs")
    async with async_engine.begin() as conn:
        rows = await conn.execute(text("""
            SELECT id, status, actions_log, error_msg, created_at, completed_at
            FROM workflow_runs
            WHERE workflow_id = :wf_id AND workspace_id = :ws
            ORDER BY created_at DESC
            LIMIT :lim
        """), {"wf_id": workflow_id, "ws": user.workspace_id, "lim": min(limit, 100)})
        runs = rows.fetchall()

    return {
        "workflow_id": workflow_id,
        "runs": [
            {
                "run_id": str(r[0]),
                "status": r[1],
                "actions_log": r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]"),
                "error_msg": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "completed_at": r[5].isoformat() if r[5] else None,
            }
            for r in runs
        ],
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Workflow routes smoke test")
        cond = WorkflowCondition(field="doc_type", operator="eq", value="invoice")
        act = WorkflowAction(type="tag", tag_value="auto-invoiced")
        req = WorkflowCreateRequest(
            name="Invoice tagger",
            trigger_event="document_ingested",
            conditions=[cond],
            actions=[act],
        )
        assert req.name == "Invoice tagger"
        assert req.trigger_event == "document_ingested"
        print("WorkflowCreateRequest validation OK")
        print("Workflow routes checks passed")

    asyncio.run(smoke())
