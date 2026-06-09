# backend/app/api/routes/onboarding.py
"""Client onboarding API — invite flow, token acceptance, wizard progress."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.auth.dependencies import (
    AuthenticatedUser,
    require_workspace_admin,
    get_current_user,
)
from app.core.invite_manager import (
    create_invite,
    validate_invite_token,
    accept_invite,
    resend_invite,
    list_invites,
    send_invite_email,
    get_onboarding_progress,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/onboarding")


# ── Send invite ───────────────────────────────────────────────────────────────


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field("editor", pattern="^(workspace_admin|editor|viewer)$")
    workspace_id: Optional[str] = None  # defaults to caller's workspace

    @field_validator("email")
    @classmethod
    def lower_email(cls, v: str) -> str:
        return v.lower()


@router.post("/invite", status_code=201)
async def send_invite(
    body: InviteRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    ws_id = body.workspace_id or user.workspace_id
    raw_token, invite = await create_invite(
        email=body.email,
        workspace_id=ws_id,
        role=body.role,
        invited_by_user_id=user.user_id,
    )
    sent = await send_invite_email(
        to_email=body.email,
        workspace_name=ws_id,
        inviter_name=user.email,
        raw_token=raw_token,
        role=body.role,
    )
    return {
        **invite,
        "email_sent": sent,
        "token_prefix": invite["token_prefix"],
    }


# ── Validate token ────────────────────────────────────────────────────────────


@router.get("/invite/{token}/validate")
async def validate_token(token: str):
    """Public — no auth required. Validates invite link before showing the form."""
    info = await validate_invite_token(token)
    if not info:
        raise HTTPException(status_code=404, detail="Invalid or expired invite token")
    return {
        "email": info["email"],
        "role": info["role"],
        "workspace_name": info["workspace_name"],
        "workspace_id": info["workspace_id"],
        "inviter_name": info.get("inviter_name"),
        "expires_at": info["expires_at"].isoformat() if info.get("expires_at") else None,
    }


# ── Accept invite ─────────────────────────────────────────────────────────────


class AcceptInviteRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=72)
    full_name: str = Field(..., min_length=1, max_length=128)


@router.post("/invite/{token}/accept")
async def accept_invite_route(token: str, body: AcceptInviteRequest):
    """Public — creates user account and returns JWT."""
    try:
        result = await accept_invite(
            raw_token=token,
            full_name=body.full_name,
            password=body.password,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


# ── Resend invite ─────────────────────────────────────────────────────────────


@router.post("/resend/{invite_id}", status_code=201)
async def resend_invite_route(
    invite_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    try:
        raw_token, invite = await resend_invite(invite_id, user.user_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    sent = await send_invite_email(
        to_email=invite["email"],
        workspace_name=invite["workspace_id"],
        inviter_name=user.email,
        raw_token=raw_token,
        role=invite["role"],
    )
    return {**invite, "email_sent": sent}


# ── List invites ──────────────────────────────────────────────────────────────


@router.get("/invites")
async def get_invites(
    workspace_id: Optional[str] = None,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    ws_id = workspace_id or user.workspace_id
    rows = await list_invites(ws_id)
    return {"invites": rows, "count": len(rows)}


# ── Onboarding progress / wizard ──────────────────────────────────────────────


@router.get("/workspace/{workspace_id}/progress")
async def onboarding_progress(
    workspace_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return onboarding checklist status."""
    progress = await get_onboarding_progress(workspace_id)
    completed = sum(1 for v in progress.values() if v)
    total = len(progress)
    return {
        "workspace_id": workspace_id,
        "checklist": progress,
        "completed_steps": completed,
        "total_steps": total,
        "percent_complete": round(completed / total * 100),
    }


class WizardStepRequest(BaseModel):
    step: int = Field(..., ge=1, le=5)
    data: dict = Field(default_factory=dict)


@router.post("/wizard/step")
async def wizard_step(
    body: WizardStepRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    5-step wizard state machine.
    Steps: 1=domain, 2=upload, 3=query, 4=api_key, 5=complete
    """
    next_step_map = {1: 2, 2: 3, 3: 4, 4: 5, 5: None}
    messages = {
        1: "Domain configured. Now upload your first document.",
        2: "Document uploaded! Try running a query.",
        3: "Great query! Copy your API key to integrate.",
        4: "API key saved. You're all set!",
        5: "Onboarding complete. Welcome to DocuMind AI!",
    }
    return {
        "step_completed": body.step,
        "next_step": next_step_map.get(body.step),
        "message": messages.get(body.step, ""),
        "workspace_id": user.workspace_id,
    }


# ── API key shortcuts (workspace_admin only) ──────────────────────────────────


@router.get("/api-keys")
async def list_workspace_api_keys(
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    from app.core.apikey_manager import list_api_keys

    keys = await list_api_keys(user.workspace_id)
    return {"api_keys": keys}


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    expires_in_days: Optional[int] = Field(None, ge=1, le=365)

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, v: list[str]) -> list[str]:
        valid = {"read", "write", "ingest", "query", "admin"}
        invalid = set(v) - valid
        if invalid:
            raise ValueError(f"Invalid scopes: {invalid}")
        return v


@router.post("/api-keys/create", status_code=201)
async def create_workspace_api_key(
    body: ApiKeyCreateRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    from app.core.apikey_manager import create_api_key

    return await create_api_key(
        workspace_id=user.workspace_id,
        name=body.name,
        scopes=body.scopes,
        created_by=user.user_id,
        expires_in_days=body.expires_in_days,
    )


@router.delete("/api-keys/{key_id}")
async def revoke_workspace_api_key(
    key_id: str,
    user: AuthenticatedUser = Depends(require_workspace_admin),
):
    from app.core.apikey_manager import revoke_api_key

    await revoke_api_key(key_id, user.workspace_id)
    return {"key_id": key_id, "revoked": True}


if __name__ == "__main__":
    print("Onboarding routes:", [r.path for r in router.routes])
