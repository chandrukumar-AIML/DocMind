# backend/app/api/routes/apikeys.py
"""API key management routes — create, list, revoke, rotate."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin
from app.core.apikey_manager import (
    create_api_key, list_api_keys, revoke_api_key,
    rotate_api_key, get_key_usage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apikeys")

_VALID_SCOPES = {"read", "write", "ingest", "query", "admin"}


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    workspace_id: Optional[str] = None
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)

    @field_validator("scopes")
    @classmethod
    def check_scopes(cls, v: list[str]) -> list[str]:
        bad = set(v) - _VALID_SCOPES
        if bad:
            raise ValueError(f"Invalid scopes: {bad}. Valid: {_VALID_SCOPES}")
        return v


@router.post("/create", status_code=201)
async def create_key(
    body: ApiKeyCreateRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    ws_id = body.workspace_id or user.workspace_id
    # Superadmin can create for any workspace; workspace_admin only for their own
    if not user.is_superuser and ws_id != user.workspace_id:
        raise HTTPException(status_code=403, detail="Cannot create keys for other workspaces")
    return await create_api_key(
        workspace_id=ws_id,
        name=body.name,
        scopes=body.scopes,
        created_by=user.user_id,
        expires_in_days=body.expires_in_days,
    )


@router.get("/list")
async def list_keys(
    workspace_id: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    ws_id = workspace_id or user.workspace_id
    if not user.is_superuser and ws_id != user.workspace_id:
        raise HTTPException(status_code=403, detail="Access denied")
    keys = await list_api_keys(ws_id)
    return {"api_keys": keys, "count": len(keys)}


@router.post("/{key_id}/revoke")
async def revoke_key_post(
    key_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    """POST /revoke — preferred endpoint for frontend compatibility."""
    try:
        await revoke_api_key(key_id, user.workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"key_id": key_id, "revoked": True}


@router.delete("/{key_id}")
async def revoke_key_delete(
    key_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    """DELETE /{key_id} — kept for REST compliance."""
    try:
        await revoke_api_key(key_id, user.workspace_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"key_id": key_id, "revoked": True}


@router.post("/{key_id}/rotate", status_code=201)
async def rotate_key(
    key_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    try:
        new_key = await rotate_api_key(key_id, user.workspace_id, user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return new_key


@router.get("/{key_id}/usage")
async def key_usage(
    key_id: str,
    days: int = Query(30, ge=1, le=90),
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    rows = await get_key_usage(key_id, days)
    return {"key_id": key_id, "days": days, "daily_usage": rows}


if __name__ == "__main__":
    print("API key routes:", [r.path for r in router.routes])
