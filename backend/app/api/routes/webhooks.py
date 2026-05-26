# backend/app/api/routes/webhooks.py
"""Webhook management: register, list, delete, test."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.webhook_dispatcher import dispatch_event, _sign_payload
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_VALID_EVENTS = {
    "document_ingested",
    "query_answered",
    "extraction_complete",
    "alert_triggered",
    "workflow_triggered",
    "annotation_created",
    "compliance_checked",
}


# ── Pydantic models ────────────────────────────────────────────

class WebhookRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=10, max_length=2048)
    secret: str = Field(..., min_length=8, max_length=128)
    events: list[str] = Field(..., min_length=1)

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: list[str]) -> list[str]:
        invalid = set(v) - _VALID_EVENTS
        if invalid:
            raise ValueError(f"Unknown events: {invalid}. Valid: {_VALID_EVENTS}")
        return list(set(v))

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class WebhookResponse(BaseModel):
    id: str
    workspace_id: str
    name: str
    url: str
    events: list[str]
    is_active: bool
    created_by: Optional[str]
    created_at: str


class WebhookTestRequest(BaseModel):
    webhook_id: str
    event_type: str = "document_ingested"


class WebhookTestResponse(BaseModel):
    webhook_id: str
    success: bool
    http_status: Optional[int]
    error: Optional[str]
    correlation_id: str


# ── Endpoints ─────────────────────────────────────────────────

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register_webhook(
    req: WebhookRegisterRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wh-reg")
    wh_id = str(uuid.uuid4())
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO webhooks
                    (id, workspace_id, name, url, secret, events, is_active, created_by)
                VALUES
                    (:id, :workspace_id, :name, :url, :secret, CAST(:events AS jsonb), TRUE, :created_by)
            """), {
                "id": wh_id,
                "workspace_id": user.workspace_id,
                "name": req.name,
                "url": req.url,
                "secret": req.secret,
                "events": json.dumps(req.events),
                "created_by": user.user_id,
            })
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to register webhook: {e}")
        raise HTTPException(status_code=500, detail="Failed to register webhook")

    return {"webhook_id": wh_id, "name": req.name, "correlation_id": corr_id}


@router.get("/list")
async def list_webhooks(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wh-list")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(text("""
                SELECT id, workspace_id, name, url, events, is_active, created_by,
                       created_at
                FROM webhooks
                WHERE workspace_id = :ws
                ORDER BY created_at DESC
            """), {"ws": user.workspace_id})
            hooks = rows.fetchall()
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to list webhooks: {e}")
        raise HTTPException(status_code=500, detail="Failed to list webhooks")

    result = []
    for row in hooks:
        result.append({
            "id": str(row[0]),
            "workspace_id": row[1],
            "name": row[2],
            "url": row[3],
            "events": row[4] if isinstance(row[4], list) else json.loads(row[4] or "[]"),
            "is_active": row[5],
            "created_by": row[6],
            "created_at": row[7].isoformat() if row[7] else None,
        })

    return {"webhooks": result, "total": len(result), "correlation_id": corr_id}


@router.delete("/{webhook_id}", status_code=status.HTTP_200_OK)
async def delete_webhook(
    webhook_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wh-del")
    try:
        async with async_engine.begin() as conn:
            result = await conn.execute(text("""
                UPDATE webhooks SET is_active = FALSE
                WHERE id = :id AND workspace_id = :ws
            """), {"id": webhook_id, "ws": user.workspace_id})
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Webhook not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to delete webhook {webhook_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete webhook")

    return {"deleted": True, "webhook_id": webhook_id, "correlation_id": corr_id}


@router.post("/test")
async def test_webhook(
    req: WebhookTestRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> WebhookTestResponse:
    corr_id = generate_correlation_id("wh-test")
    try:
        async with async_engine.begin() as conn:
            row = await conn.execute(text("""
                SELECT id, url, secret FROM webhooks
                WHERE id = :id AND workspace_id = :ws AND is_active = TRUE
            """), {"id": req.webhook_id, "ws": user.workspace_id})
            hook = row.fetchone()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")

    if not hook:
        raise HTTPException(status_code=404, detail="Webhook not found or inactive")

    _, url, secret = hook
    from app.core.webhook_dispatcher import _deliver_once
    test_payload = {
        "event_type": req.event_type,
        "workspace_id": user.workspace_id,
        "correlation_id": corr_id,
        "data": {"test": True, "message": "DocuMind webhook test ping"},
    }

    try:
        success, http_status, error = await _deliver_once(url, secret, test_payload, corr_id)
    except Exception as e:
        success, http_status, error = False, None, str(e)[:200]

    return WebhookTestResponse(
        webhook_id=req.webhook_id,
        success=success,
        http_status=http_status,
        error=error,
        correlation_id=corr_id,
    )


@router.get("/deliveries/{webhook_id}")
async def get_delivery_history(
    webhook_id: str,
    limit: int = 50,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("wh-hist")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(text("""
                SELECT id, event_type, attempt, status, http_status, error_msg,
                       delivered_at, created_at
                FROM webhook_deliveries
                WHERE webhook_id = :wid AND workspace_id = :ws
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"wid": webhook_id, "ws": user.workspace_id, "lim": min(limit, 200)})
            deliveries = rows.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch deliveries: {e}")

    return {
        "webhook_id": webhook_id,
        "deliveries": [
            {
                "id": str(d[0]),
                "event_type": d[1],
                "attempt": d[2],
                "status": d[3],
                "http_status": d[4],
                "error_msg": d[5],
                "delivered_at": d[6].isoformat() if d[6] else None,
                "created_at": d[7].isoformat() if d[7] else None,
            }
            for d in deliveries
        ],
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke_test():
        print("Webhook routes smoke test")
        from app.core.webhook_dispatcher import _sign_payload
        body = b'{"test":true}'
        sig = _sign_payload(body, "my-secret")
        assert sig.startswith("sha256="), f"Bad sig: {sig}"
        print(f"Signature OK: {sig[:20]}...")
        print("All webhook route checks passed")

    asyncio.run(smoke_test())
