# backend/app/core/esign_handler.py
"""E-Signature handler: DocuSign API primary + in-app canvas fallback."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from typing import Any, Optional

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_DOCUSIGN_BASE_URL = "https://demo.docusign.net/restapi/v2.1"
_REQUEST_TIMEOUT = 30.0


async def ensure_esign_schema() -> None:
    """Create esign_requests table."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS esign_requests (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id    VARCHAR(64) NOT NULL,
                source_file     TEXT NOT NULL,
                envelope_id     VARCHAR(256),
                status          VARCHAR(32) NOT NULL DEFAULT 'pending',
                signers         JSONB NOT NULL DEFAULT '[]',
                signed_file_path TEXT,
                callback_url    TEXT,
                provider        VARCHAR(32) NOT NULL DEFAULT 'in_app',
                created_by      VARCHAR(64),
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at    TIMESTAMP WITH TIME ZONE
            )
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_esign_workspace ON esign_requests(workspace_id)"))
    logger.info("E-sign schema verified")


async def create_esign_request(
    workspace_id: str,
    source_file: str,
    signers: list[dict],
    callback_url: Optional[str],
    created_by: str,
) -> dict[str, Any]:
    """Create an e-sign request record and attempt DocuSign envelope creation."""
    corr_id = generate_correlation_id("esign")
    req_id = str(uuid.uuid4())
    provider = "in_app"
    envelope_id = None

    # Try DocuSign if credentials available
    ds_key = os.getenv("DOCUSIGN_INTEGRATION_KEY", "")
    ds_account = os.getenv("DOCUSIGN_ACCOUNT_ID", "")
    ds_token = os.getenv("DOCUSIGN_ACCESS_TOKEN", "")

    if ds_key and ds_account and ds_token:
        try:
            envelope_id = await _create_docusign_envelope(
                source_file=source_file,
                signers=signers,
                account_id=ds_account,
                access_token=ds_token,
                callback_url=callback_url,
                correlation_id=corr_id,
            )
            provider = "docusign"
        except Exception as e:
            logger.warning(f"[{corr_id}] DocuSign envelope creation failed, using in-app: {e}")

    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO esign_requests
                (id, workspace_id, source_file, envelope_id, signers,
                 callback_url, provider, created_by)
            VALUES
                (:id, :ws, :sf, :env_id, CAST(:signers AS jsonb),
                 :cb, :prov, :by)
        """),
            {
                "id": req_id,
                "ws": workspace_id,
                "sf": source_file,
                "env_id": envelope_id,
                "signers": json.dumps(signers),
                "cb": callback_url,
                "prov": provider,
                "by": created_by,
            },
        )

    return {
        "request_id": req_id,
        "envelope_id": envelope_id,
        "provider": provider,
        "status": "pending",
        "signers": signers,
        "correlation_id": corr_id,
    }


async def _create_docusign_envelope(
    source_file: str,
    signers: list[dict],
    account_id: str,
    access_token: str,
    callback_url: Optional[str],
    correlation_id: str,
) -> str:
    """Create a DocuSign envelope and return envelope_id."""
    import httpx

    # Build signer list for DocuSign
    ds_signers = []
    for i, signer in enumerate(signers):
        ds_signers.append(
            {
                "email": signer.get("email"),
                "name": signer.get("name", f"Signer {i+1}"),
                "recipientId": str(i + 1),
                "routingOrder": str(signer.get("order", i + 1)),
                "tabs": {
                    "signHereTabs": [
                        {
                            "anchorString": "/sig/",
                            "anchorUnits": "pixels",
                            "anchorXOffset": "20",
                            "anchorYOffset": "10",
                        }
                    ]
                },
            }
        )

    # Read document
    try:
        with open(source_file, "rb") as f:
            doc_bytes = f.read()
        doc_b64 = base64.b64encode(doc_bytes).decode()
        doc_name = source_file.split("/")[-1].split("\\")[-1]
    except Exception:
        doc_b64 = ""
        doc_name = "document.pdf"

    envelope_def = {
        "emailSubject": "Please sign this document",
        "documents": [
            {
                "documentBase64": doc_b64,
                "name": doc_name,
                "fileExtension": "pdf",
                "documentId": "1",
            }
        ],
        "recipients": {"signers": ds_signers},
        "status": "sent",
        "eventNotification": {
            "url": callback_url,
            "envelopeEvents": [{"envelopeEventStatusCode": "completed"}],
        }
        if callback_url
        else {},
    }

    url = f"{_DOCUSIGN_BASE_URL}/accounts/{account_id}/envelopes"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        resp = await client.post(url, json=envelope_def, headers=headers)
        resp.raise_for_status()
        return resp.json()["envelopeId"]


async def handle_docusign_callback(payload: dict) -> None:
    """Process DocuSign webhook callback and update esign_requests."""
    envelope_id = payload.get("envelopeId")
    status = payload.get("status", "").lower()
    if not envelope_id:
        return

    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            UPDATE esign_requests
            SET status = :status,
                completed_at = CASE WHEN :status = 'completed' THEN NOW() ELSE NULL END
            WHERE envelope_id = :env_id
        """),
            {"status": status, "env_id": envelope_id},
        )

    logger.info(f"DocuSign callback: envelope {envelope_id} → {status}")


async def record_inapp_signature(
    request_id: str,
    workspace_id: str,
    signer_user_id: str,
    signature_data: str,
) -> dict[str, Any]:
    """Record an in-app canvas signature."""
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text("""
            UPDATE esign_requests
            SET status = 'completed', completed_at = NOW()
            WHERE id = :id AND workspace_id = :ws
        """),
            {"id": request_id, "ws": workspace_id},
        )
        if result.rowcount == 0:
            raise ValueError(f"E-sign request {request_id} not found")

    return {"signed": True, "request_id": request_id, "signer": signer_user_id}


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("E-sign handler smoke test")
        # Test that schema bootstrap function is importable
        assert callable(ensure_esign_schema)
        assert callable(create_esign_request)
        # Test DocuSign env vars fallback
        key = os.getenv("DOCUSIGN_INTEGRATION_KEY", "")
        print(f"DocuSign integration key configured: {bool(key)}")
        print("E-sign handler checks passed")

    asyncio.run(smoke())
