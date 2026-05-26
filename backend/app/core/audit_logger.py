# backend/app/core/audit_logger.py
"""
Audit logger — records all security-relevant actions with workspace, user,
IP, and request context. Fire-and-forget (never raises).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import text

from app.database.engine import async_engine

logger = logging.getLogger(__name__)

# ── Canonical action names ────────────────────────────────────────────────────
A_USER_LOGIN              = "user_login"
A_USER_LOGOUT             = "user_logout"
A_LOGIN_FAILED            = "login_failed"
A_DOCUMENT_UPLOADED       = "document_uploaded"
A_DOCUMENT_DELETED        = "document_deleted"
A_DOCUMENT_DOWNLOADED     = "document_downloaded"
A_QUERY_EXECUTED          = "query_executed"
A_AGENT_QUERY_EXECUTED    = "agent_query_executed"
A_API_KEY_CREATED         = "api_key_created"
A_API_KEY_REVOKED         = "api_key_revoked"
A_WORKSPACE_SETTINGS_CHANGED = "workspace_settings_changed"
A_USER_INVITED            = "user_invited"
A_USER_ROLE_CHANGED       = "user_role_changed"
A_COMPLIANCE_CHECK_RUN    = "compliance_check_run"
A_WEBHOOK_TRIGGERED       = "webhook_triggered"
A_WORKSPACE_SUSPENDED     = "workspace_suspended"
A_WORKSPACE_ACTIVATED     = "workspace_activated"
A_IMPERSONATION_STARTED   = "impersonation_started"


# ── Schema bootstrap ─────────────────────────────────────────────────────────

async def ensure_audit_schema() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id    UUID REFERENCES workspaces(id) ON DELETE SET NULL,
                user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
                action          VARCHAR(100) NOT NULL,
                resource_type   VARCHAR(50),
                resource_id     VARCHAR(255),
                ip_address      VARCHAR(45),
                user_agent      TEXT,
                request_data    JSONB DEFAULT '{}',
                response_status INTEGER,
                severity        VARCHAR(10) DEFAULT 'info',
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_audit_log_workspace_id ON audit_log(workspace_id)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_user_id ON audit_log(user_id)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_action ON audit_log(action)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log(created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_ws_date ON audit_log(workspace_id, created_at)",
        ]:
            await conn.execute(text(idx_sql))


# ── Core log function ─────────────────────────────────────────────────────────

async def log_event(
    action: str,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_data: Optional[dict] = None,
    response_status: Optional[int] = None,
    severity: str = "info",
) -> None:
    """
    Log an audit event. Never raises — failures are suppressed to avoid
    breaking the primary request flow.
    """
    try:
        req_json = json.dumps(request_data or {})
        async with async_engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO audit_log
                    (workspace_id, user_id, action, resource_type, resource_id,
                     ip_address, user_agent, request_data, response_status, severity)
                VALUES
                    (:ws_id, :user_id, :action, :res_type, :res_id,
                     :ip, :ua, CAST(:req_data AS jsonb), :status, :severity)
            """), {
                "ws_id": workspace_id,
                "user_id": user_id,
                "action": action,
                "res_type": resource_type,
                "res_id": resource_id,
                "ip": ip_address,
                "ua": user_agent,
                "req_data": req_json,
                "status": response_status,
                "severity": severity,
            })
    except Exception as e:
        logger.warning(f"[audit_logger] Failed to log '{action}': {e}")


def log_event_bg(
    action: str,
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Fire-and-forget wrapper — safe to call from sync code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(log_event(action, workspace_id, user_id, **kwargs))
        else:
            loop.run_until_complete(log_event(action, workspace_id, user_id, **kwargs))
    except Exception as e:
        logger.warning(f"[audit_logger] bg log failed: {e}")


# ── Query helpers ─────────────────────────────────────────────────────────────

async def query_audit_log(
    workspace_id: Optional[str] = None,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    severity: Optional[str] = None,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filters = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if workspace_id:
        filters.append("al.workspace_id = :wsid")
        params["wsid"] = workspace_id
    if user_id:
        filters.append("al.user_id = :uid")
        params["uid"] = user_id
    if action:
        filters.append("al.action = :action")
        params["action"] = action
    if severity:
        filters.append("al.severity = :severity")
        params["severity"] = severity
    if from_dt:
        filters.append("al.created_at >= :from_dt")
        params["from_dt"] = from_dt
    if to_dt:
        filters.append("al.created_at <= :to_dt")
        params["to_dt"] = to_dt

    where = " AND ".join(filters)
    async with async_engine.connect() as conn:
        rows = (await conn.execute(text(f"""
            SELECT al.id::text, al.action, al.resource_type, al.resource_id,
                   al.ip_address, al.response_status, al.severity, al.created_at,
                   u.email AS user_email
            FROM audit_log al
            LEFT JOIN users u ON u.id = al.user_id
            WHERE {where}
            ORDER BY al.created_at DESC
            LIMIT :limit OFFSET :offset
        """), params)).mappings().fetchall()
        return [dict(r) for r in rows]


async def export_audit_csv(workspace_id: str) -> str:
    import csv, io
    rows = await query_audit_log(workspace_id=workspace_id, limit=10000)
    buf = io.StringIO()
    if not rows:
        return "id,action,user_email,resource_type,resource_id,ip_address,severity,created_at\n"
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    for r in rows:
        writer.writerow({k: str(v) if v is not None else "" for k, v in r.items()})
    return buf.getvalue()


if __name__ == "__main__":
    import asyncio

    async def _test():
        print("Testing audit_logger…")
        await ensure_audit_schema()
        print("  Schema ensured.")
        # Verify fire-and-forget doesn't raise
        await log_event(
            action=A_USER_LOGIN,
            workspace_id="00000000-0000-0000-0000-000000000001",
            user_id=None,
            ip_address="127.0.0.1",
        )
        print("  Event logged (or silently suppressed).")
        print("Done.")

    asyncio.run(_test())
