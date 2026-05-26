# backend/app/core/webhook_dispatcher.py
"""
Webhook dispatcher: HMAC-SHA256 signed delivery with exponential backoff retry
and PostgreSQL dead-letter queue for failed events.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import text

from app.database.engine import async_engine
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# Webhook delivery settings
_MAX_RETRIES = 3
_BASE_DELAY = 1.0          # seconds (doubles each attempt)
_DELIVERY_TIMEOUT = 10.0   # seconds per HTTP call
_MAX_PAYLOAD_BYTES = 64 * 1024  # 64 KB safety cap


# ── Schema bootstrap ──────────────────────────────────────────────────────

async def ensure_webhook_schema() -> None:
    """Create webhooks and webhook_deliveries tables if they don't exist."""
    async with async_engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect != "postgresql":
            logger.warning("Webhook schema bootstrap skipped: not PostgreSQL")
            return

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS webhooks (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id  VARCHAR(64)  NOT NULL,
                name          VARCHAR(128) NOT NULL,
                url           TEXT         NOT NULL,
                secret        VARCHAR(128) NOT NULL,
                events        JSONB        NOT NULL DEFAULT '[]',
                is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
                created_by    VARCHAR(64),
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                webhook_id    UUID         NOT NULL,
                workspace_id  VARCHAR(64)  NOT NULL,
                event_type    VARCHAR(64)  NOT NULL,
                payload       JSONB        NOT NULL,
                attempt       INTEGER      NOT NULL DEFAULT 1,
                status        VARCHAR(32)  NOT NULL DEFAULT 'pending',
                http_status   INTEGER,
                error_msg     TEXT,
                delivered_at  TIMESTAMP WITH TIME ZONE,
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Indexes for fast lookups
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_webhooks_workspace ON webhooks(workspace_id)",
            "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_webhook ON webhook_deliveries(webhook_id)",
            "CREATE INDEX IF NOT EXISTS ix_webhook_deliveries_status ON webhook_deliveries(status)",
        ]:
            await conn.execute(text(idx_sql))

    logger.info("Webhook schema verified")


# ── Signature ─────────────────────────────────────────────────────────────

def _sign_payload(payload_bytes: bytes, secret: str) -> str:
    """Return 'sha256=<hex>' HMAC-SHA256 signature (GitHub-style)."""
    sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ── Core delivery ─────────────────────────────────────────────────────────

async def _deliver_once(
    url: str,
    secret: str,
    payload: dict[str, Any],
    delivery_id: str,
) -> tuple[bool, int | None, str | None]:
    """Single HTTP POST attempt. Returns (success, http_status, error_msg)."""
    body = json.dumps(payload, default=str).encode("utf-8")
    if len(body) > _MAX_PAYLOAD_BYTES:
        return False, None, f"Payload exceeds {_MAX_PAYLOAD_BYTES} bytes"

    headers = {
        "Content-Type": "application/json",
        "X-DocuMind-Event": payload.get("event_type", "unknown"),
        "X-DocuMind-Delivery": delivery_id,
        "X-DocuMind-Signature-256": _sign_payload(body, secret),
        "User-Agent": "DocuMind-Webhooks/2.0",
    }

    try:
        async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT) as client:
            resp = await client.post(url, content=body, headers=headers)
            success = 200 <= resp.status_code < 300
            return success, resp.status_code, None if success else f"HTTP {resp.status_code}"
    except httpx.TimeoutException:
        return False, None, "Delivery timeout"
    except Exception as e:
        return False, None, str(e)[:200]


async def _log_delivery(
    webhook_id: str,
    workspace_id: str,
    event_type: str,
    payload: dict,
    attempt: int,
    status: str,
    http_status: int | None,
    error_msg: str | None,
) -> None:
    """Persist delivery record (success or DLQ entry) to PostgreSQL."""
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO webhook_deliveries
                    (id, webhook_id, workspace_id, event_type, payload,
                     attempt, status, http_status, error_msg, delivered_at)
                VALUES
                    (:id, :webhook_id, :workspace_id, :event_type, CAST(:payload AS jsonb),
                     :attempt, :status, :http_status, :error_msg,
                     CASE WHEN :status = 'delivered' THEN NOW() ELSE NULL END)
            """), {
                "id": str(uuid.uuid4()),
                "webhook_id": webhook_id,
                "workspace_id": workspace_id,
                "event_type": event_type,
                "payload": json.dumps(payload, default=str),
                "attempt": attempt,
                "status": status,
                "http_status": http_status,
                "error_msg": error_msg,
            })
    except Exception as e:
        logger.warning(f"Could not log webhook delivery: {e}")


async def dispatch_event(
    workspace_id: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """
    Fire-and-forget: retrieve all active webhooks for this workspace + event,
    then deliver with retry. Call with asyncio.create_task() to not block.
    """
    corr_id = generate_correlation_id("webhook")
    payload = {
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workspace_id": workspace_id,
        "correlation_id": corr_id,
        "data": data,
    }

    # Load matching webhooks
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(text("""
                SELECT id, url, secret FROM webhooks
                WHERE workspace_id = :ws
                  AND is_active = TRUE
                  AND events @> CAST(:event_json AS jsonb)
            """), {
                "ws": workspace_id,
                "event_json": json.dumps([event_type]),
            })
            hooks = rows.fetchall()
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not load webhooks for dispatch: {e}")
        return

    if not hooks:
        return

    for hook_id, url, secret in hooks:
        delivery_id = str(uuid.uuid4())
        success = False
        last_status = None
        last_error = None

        for attempt in range(1, _MAX_RETRIES + 1):
            if attempt > 1:
                delay = _BASE_DELAY * (2 ** (attempt - 2))
                await asyncio.sleep(delay)

            success, last_status, last_error = await _deliver_once(
                url, secret, payload, delivery_id
            )
            if success:
                break
            logger.warning(
                f"[{corr_id}] Webhook {str(hook_id)[:8]} attempt {attempt}/{_MAX_RETRIES} "
                f"failed: {last_error}"
            )

        final_status = "delivered" if success else "failed"
        await _log_delivery(
            str(hook_id), workspace_id, event_type, payload,
            _MAX_RETRIES if not success else 1,
            final_status, last_status, last_error,
        )
        if not success:
            logger.error(
                f"[{corr_id}] Webhook {str(hook_id)[:8]} dead-lettered after "
                f"{_MAX_RETRIES} attempts: {last_error}"
            )


__all__ = ["dispatch_event", "ensure_webhook_schema", "_sign_payload"]
