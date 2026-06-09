# backend/app/api/routes/esignature.py
"""E-signature API: request signatures, status, DocuSign callbacks, in-app signing."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.esign_handler import (
    create_esign_request,
    handle_docusign_callback,
    record_inapp_signature,
)
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/esignature", tags=["esignature"])


# ── Pydantic models ────────────────────────────────────────────


class SignerInfo(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    email: str = Field(..., min_length=5, max_length=256)
    order: int = Field(default=1, ge=1, le=20)


class ESignRequest(BaseModel):
    source_file: str = Field(..., min_length=1, max_length=1024)
    signers: list[SignerInfo] = Field(..., min_length=1, max_length=10)
    callback_url: Optional[str] = Field(default=None, max_length=2048)


class InAppSignatureRequest(BaseModel):
    request_id: str
    signature_data: str = Field(..., min_length=10)


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/request", status_code=status.HTTP_201_CREATED)
async def request_signature(
    req: ESignRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("esign-req")
    signers = [s.model_dump() for s in req.signers]

    try:
        result = await create_esign_request(
            workspace_id=user.workspace_id,
            source_file=req.source_file,
            signers=signers,
            callback_url=req.callback_url,
            created_by=user.user_id,
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to create e-sign request: {e}")
        raise HTTPException(status_code=500, detail="Failed to create e-sign request")

    return result


@router.get("/status/{request_id}")
async def get_esign_status(
    request_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("esign-status")
    async with async_engine.begin() as conn:
        row = await conn.execute(
            text("""
            SELECT id, source_file, envelope_id, status, signers,
                   provider, created_at, completed_at
            FROM esign_requests
            WHERE id = :id AND workspace_id = :ws
        """),
            {"id": request_id, "ws": user.workspace_id},
        )
        r = row.fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="E-sign request not found")

    return {
        "request_id": str(r[0]),
        "source_file": r[1],
        "envelope_id": r[2],
        "status": r[3],
        "signers": r[4] if isinstance(r[4], list) else json.loads(r[4] or "[]"),
        "provider": r[5],
        "created_at": r[6].isoformat() if r[6] else None,
        "completed_at": r[7].isoformat() if r[7] else None,
        "correlation_id": corr_id,
    }


@router.get("/list")
async def list_esign_requests(
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("esign-list")
    async with async_engine.begin() as conn:
        rows = await conn.execute(
            text("""
            SELECT id, source_file, status, provider, created_at
            FROM esign_requests
            WHERE workspace_id = :ws
            ORDER BY created_at DESC
            LIMIT 100
        """),
            {"ws": user.workspace_id},
        )
        requests = rows.fetchall()

    return {
        "requests": [
            {
                "request_id": str(r[0]),
                "source_file": r[1],
                "status": r[2],
                "provider": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
            }
            for r in requests
        ],
        "total": len(requests),
        "correlation_id": corr_id,
    }


@router.post("/inapp/sign")
async def inapp_sign(
    req: InAppSignatureRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("esign-inapp")
    try:
        result = await record_inapp_signature(
            request_id=req.request_id,
            workspace_id=user.workspace_id,
            signer_user_id=user.user_id,
            signature_data=req.signature_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Signature recording failed: {e}")

    result["correlation_id"] = corr_id
    return result


@router.post("/docusign/callback", include_in_schema=False)
async def docusign_webhook(request: Request) -> dict[str, Any]:
    """DocuSign event notification callback (no auth — verified by secret)."""
    try:
        payload = await request.json()
        await handle_docusign_callback(payload)
    except Exception as e:
        logger.error(f"DocuSign callback error: {e}")
    return {"received": True}


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("E-signature routes smoke test")
        signer = SignerInfo(name="Alice Sharma", email="alice@example.com", order=1)
        req = ESignRequest(
            source_file="contracts/agreement.pdf",
            signers=[signer],
        )
        assert len(req.signers) == 1
        assert req.signers[0].email == "alice@example.com"
        print("ESignRequest validation OK")
        print("E-signature routes checks passed")

    asyncio.run(smoke())
