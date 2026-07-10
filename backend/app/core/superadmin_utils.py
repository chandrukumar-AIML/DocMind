"""
Superadmin utilities — cross-workspace analytics, workspace CRUD,
impersonation tokens, system health, billing aggregation.
"""

from __future__ import annotations

import csv
import io
import logging
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_IMPERSONATION_TTL_HOURS = 1


# ── Schema bootstrap ─────────────────────────────────────────────────────────


async def ensure_superadmin_schema() -> None:
    """Create impersonation_tokens table if absent."""
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS impersonation_tokens (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                token_hash      VARCHAR(255) UNIQUE NOT NULL,
                issued_by       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                target_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
                used_at         TIMESTAMP WITH TIME ZONE,
                revoked         BOOLEAN DEFAULT FALSE,
                created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_impersonation_expires " "ON impersonation_tokens(expires_at)")
        )


# ── Overview / stats ─────────────────────────────────────────────────────────


async def get_system_stats() -> dict[str, Any]:
    """Cross-workspace aggregate stats for the overview cards."""
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT
                (SELECT COUNT(*) FROM workspaces)                           AS total_workspaces,
                (SELECT COUNT(*) FROM workspaces WHERE is_active = TRUE)    AS active_workspaces,
                (SELECT COUNT(*) FROM users WHERE is_active = TRUE)         AS total_users,
                (SELECT COUNT(*) FROM users WHERE is_superuser = TRUE)      AS superadmin_count,
                (SELECT COALESCE(SUM(doc_count),0) FROM workspaces)         AS total_documents,
                (SELECT COALESCE(SUM(query_count_today),0) FROM workspaces) AS total_queries_today,
                (SELECT COUNT(*) FROM audit_log
                    WHERE created_at > NOW() - INTERVAL '24 hours')         AS audit_events_24h,
                (SELECT COUNT(*) FROM invites WHERE status = 'pending')     AS pending_invites,
                (SELECT COUNT(*) FROM api_keys WHERE is_active = TRUE)      AS active_api_keys
        """)
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else {}


async def get_top_workspaces(limit: int = 5) -> list[dict[str, Any]]:
    """Top N workspaces by query volume today."""
    async with async_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
            SELECT id::text AS workspace_id, name, client_name,
                   query_count_today, doc_count, plan, is_active
            FROM workspaces
            ORDER BY query_count_today DESC
            LIMIT :limit
        """),
                    {"limit": limit},
                )
            )
            .mappings()
            .fetchall()
        )
        return [dict(r) for r in rows]


# ── Workspace listing / detail ────────────────────────────────────────────────


async def list_all_workspaces(
    search: str | None = None,
    plan: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    filters = ["1=1"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if search:
        filters.append("(name ILIKE :search OR client_name ILIKE :search OR client_email ILIKE :search)")
        params["search"] = f"%{search}%"
    if plan:
        filters.append("plan = :plan")
        params["plan"] = plan
    if is_active is not None:
        filters.append("is_active = :is_active")
        params["is_active"] = is_active

    where = " AND ".join(filters)

    async with async_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
            SELECT
                id::text            AS workspace_id,
                name,
                slug,
                client_name,
                client_email,
                plan,
                is_active,
                doc_count,
                query_count_today,
                storage_used_mb,
                max_docs,
                max_queries_per_day,
                max_storage_gb,
                domain_type,
                suspended_at,
                suspended_reason,
                created_at
            FROM workspaces
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """),
                    params,
                )
            )
            .mappings()
            .fetchall()
        )

        result = []
        for r in rows:
            d = dict(r)
            cnt = (
                await conn.execute(
                    text("""
                SELECT COUNT(*) FROM workspace_members
                WHERE workspace_id = :wsid AND is_active = TRUE
            """),
                    {"wsid": r["workspace_id"]},
                )
            ).scalar() or 0
            d["active_users"] = cnt
            result.append(d)
        return result


async def get_workspace_detail(workspace_id: str) -> dict[str, Any] | None:
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT w.*,
                   (SELECT COUNT(*) FROM workspace_members m
                    WHERE m.workspace_id = w.id AND m.is_active) AS active_users,
                   (SELECT COUNT(*) FROM api_keys k
                    WHERE k.workspace_id = w.id AND k.is_active) AS active_keys
            FROM workspaces w
            WHERE w.id = :wsid
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else None


# ── Workspace create / update ─────────────────────────────────────────────────


async def create_workspace_for_client(
    client_name: str,
    client_email: str,
    plan: str,
    domain_type: str | None,
    max_docs: int,
    max_queries_per_day: int,
    max_storage_gb: float,
) -> dict[str, Any]:
    """Create workspace and return its data."""
    import re
    import uuid as _uuid

    slug_base = re.sub(r"[^a-z0-9]+", "-", client_name.lower()).strip("-")
    if len(slug_base) < 3:
        slug_base = f"ws-{slug_base}"
    slug = f"{slug_base[:40]}-{_uuid.uuid4().hex[:6]}"

    async with async_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            INSERT INTO workspaces
                (name, slug, client_name, client_email, plan, domain_type,
                 max_docs, max_queries_per_day, max_storage_gb, is_active)
            VALUES
                (:name, :slug, :client_name, :client_email, :plan, :domain_type,
                 :max_docs, :max_queries_per_day, :max_storage_gb, TRUE)
            RETURNING id::text, name, slug, client_email, plan
        """),
                    {
                        "name": client_name,
                        "slug": slug,
                        "client_name": client_name,
                        "client_email": client_email,
                        "plan": plan,
                        "domain_type": domain_type,
                        "max_docs": max_docs,
                        "max_queries_per_day": max_queries_per_day,
                        "max_storage_gb": max_storage_gb,
                    },
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row)


async def update_workspace_limits(
    workspace_id: str,
    max_docs: int | None = None,
    max_queries_per_day: int | None = None,
    max_storage_gb: float | None = None,
    plan: str | None = None,
) -> dict[str, Any]:
    sets: list[str] = []
    params: dict[str, Any] = {"wsid": workspace_id}
    if max_docs is not None:
        sets.append("max_docs = :max_docs")
        params["max_docs"] = max_docs
    if max_queries_per_day is not None:
        sets.append("max_queries_per_day = :max_qpd")
        params["max_qpd"] = max_queries_per_day
    if max_storage_gb is not None:
        sets.append("max_storage_gb = :max_storage")
        params["max_storage"] = max_storage_gb
    if plan is not None:
        sets.append("plan = :plan")
        params["plan"] = plan
    if not sets:
        return {}

    async with async_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(f"""
            UPDATE workspaces SET {', '.join(sets)}
            WHERE id = :wsid
            RETURNING id::text, max_docs, max_queries_per_day, max_storage_gb, plan
        """),
                    params,
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else {}


# ── Suspend / activate ────────────────────────────────────────────────────────


async def suspend_workspace(workspace_id: str, reason: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            UPDATE workspaces
            SET is_active = FALSE,
                suspended_at = NOW(),
                suspended_reason = :reason
            WHERE id = :wsid
        """),
            {"wsid": workspace_id, "reason": reason},
        )


async def activate_workspace(workspace_id: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            UPDATE workspaces
            SET is_active = TRUE,
                suspended_at = NULL,
                suspended_reason = NULL
            WHERE id = :wsid
        """),
            {"wsid": workspace_id},
        )


# ── Impersonation ─────────────────────────────────────────────────────────────


async def create_impersonation_token(
    issued_by_user_id: str,
    target_workspace_id: str,
) -> dict[str, Any]:
    """Generate a 1-hour JWT scoped to the workspace_admin of the target workspace."""
    from app.auth.jwt_handler import create_access_token

    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT u.id::text AS user_id, u.email, m.role
            FROM workspace_members m
            JOIN users u ON u.id = m.user_id
            WHERE m.workspace_id = :wsid
              AND m.role IN ('workspace_admin', 'admin')
              AND m.is_active = TRUE
            ORDER BY m.joined_at ASC
            LIMIT 1
        """),
                    {"wsid": target_workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise ValueError(f"No workspace_admin found for workspace {target_workspace_id}")

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=_IMPERSONATION_TTL_HOURS)

    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO impersonation_tokens
                (token_hash, issued_by, target_user_id, workspace_id, expires_at)
            VALUES (:hash, :issued_by, :target, :wsid, :expires_at)
        """),
            {
                "hash": token_hash,
                "issued_by": issued_by_user_id,
                "target": row["user_id"],
                "wsid": target_workspace_id,
                "expires_at": expires_at,
            },
        )

    jwt_token = create_access_token(
        user_id=row["user_id"],
        email=row["email"],
        workspace_id=target_workspace_id,
        role=row["role"],
        expires_delta=timedelta(hours=_IMPERSONATION_TTL_HOURS),
    )

    return {
        "token": jwt_token,
        "target_email": row["email"],
        "target_role": row["role"],
        "expires_at": expires_at.isoformat(),
        "note": "Impersonation token — expires in 1 hour",
    }


# ── Workspace billing ─────────────────────────────────────────────────────────


async def get_workspace_billing(workspace_id: str) -> dict[str, Any]:
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT
                w.id::text AS workspace_id,
                w.client_name, w.plan,
                w.doc_count, w.storage_used_mb,
                w.max_docs, w.max_storage_gb,
                COALESCE(SUM(ul.tokens_used), 0)  AS total_tokens_this_month,
                COALESCE(SUM(ul.ocr_pages), 0)    AS total_ocr_pages,
                COUNT(CASE WHEN ul.action_type IN ('query_executed','agent_query','graph_query') THEN 1 END)
                    AS total_queries_this_month
            FROM workspaces w
            LEFT JOIN usage_logs ul
                ON ul.workspace_id = w.id
               AND ul.created_at >= DATE_TRUNC('month', NOW())
            WHERE w.id = :wsid
            GROUP BY w.id, w.client_name, w.plan, w.doc_count,
                     w.storage_used_mb, w.max_docs, w.max_storage_gb
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else {}


# ── Audit log retrieval ───────────────────────────────────────────────────────


async def get_workspace_audit_log(
    workspace_id: str,
    limit: int = 1000,
    action: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
) -> list[dict[str, Any]]:
    filters = ["al.workspace_id = :wsid"]
    params: dict[str, Any] = {"wsid": workspace_id, "limit": limit}
    if action:
        filters.append("al.action = :action")
        params["action"] = action
    if from_dt:
        filters.append("al.created_at >= :from_dt")
        params["from_dt"] = from_dt
    if to_dt:
        filters.append("al.created_at <= :to_dt")
        params["to_dt"] = to_dt

    where = " AND ".join(filters)
    async with async_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text(f"""
            SELECT al.id::text, al.action, al.resource_type, al.resource_id,
                   al.ip_address, al.response_status, al.severity, al.created_at,
                   u.email AS user_email
            FROM audit_log al
            LEFT JOIN users u ON u.id = al.user_id
            WHERE {where}
            ORDER BY al.created_at DESC
            LIMIT :limit
        """),
                    params,
                )
            )
            .mappings()
            .fetchall()
        )
        return [dict(r) for r in rows]


# ── Billing CSV export ────────────────────────────────────────────────────────


async def export_billing_csv(month: str | None = None) -> str:
    """Return CSV string of per-workspace monthly usage."""
    if not month:
        now = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")

    year_str, month_str = month.split("-")

    async with async_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
            SELECT
                w.id::text       AS workspace_id,
                w.client_name,
                w.plan,
                COALESCE(SUM(ul.tokens_used), 0)       AS llm_tokens_used,
                COALESCE(SUM(ul.ocr_pages), 0)         AS ocr_pages,
                COALESCE(SUM(ul.storage_delta_mb), 0)  AS storage_added_mb,
                COUNT(CASE WHEN ul.action_type = 'document_uploaded' THEN 1 END)
                    AS docs_processed,
                COUNT(CASE WHEN ul.action_type IN
                    ('query_executed','agent_query','graph_query') THEN 1 END)
                    AS queries_run,
                w.storage_used_mb AS current_storage_mb
            FROM workspaces w
            LEFT JOIN usage_logs ul
                ON ul.workspace_id = w.id
               AND EXTRACT(YEAR FROM ul.created_at) = :year
               AND EXTRACT(MONTH FROM ul.created_at) = :month
            GROUP BY w.id, w.client_name, w.plan, w.storage_used_mb
            ORDER BY queries_run DESC
        """),
                    {"year": int(year_str), "month": int(month_str)},
                )
            )
            .mappings()
            .fetchall()
        )

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "workspace_id",
            "client_name",
            "plan",
            "docs_processed",
            "queries_run",
            "ocr_pages",
            "llm_tokens_used",
            "storage_added_mb",
            "current_storage_mb",
        ],
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r[k] for k in writer.fieldnames})
    return buf.getvalue()


# ── System health ─────────────────────────────────────────────────────────────


async def get_system_health() -> dict[str, Any]:
    services: dict[str, str] = {}

    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        services["postgres"] = "ok"
    except Exception as e:
        services["postgres"] = f"error: {e}"

    try:
        from app.cache import get_cache

        cache = await get_cache()
        if cache and hasattr(cache, "ping"):
            await cache.ping()
        services["redis"] = "ok"
    except Exception as e:
        services["redis"] = f"error: {e}"

    try:
        from app.core.celery_app import celery_app

        i = celery_app.control.inspect(timeout=2)
        active = i.active()
        services["celery"] = "ok" if active is not None else "no_workers"
    except Exception as e:
        services["celery"] = f"error: {e}"

    overall = "healthy" if all(v == "ok" for v in services.values()) else "degraded"
    return {
        "status": overall,
        "services": services,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_celery_stats() -> dict[str, Any]:
    try:
        from app.core.celery_app import celery_app

        i = celery_app.control.inspect(timeout=3)
        active = i.active() or {}
        workers = list(active.keys())
        active_tasks = sum(len(t) for t in active.values())
        return {
            "worker_count": len(workers),
            "active_tasks": active_tasks,
            "workers": workers,
        }
    except Exception as e:
        return {"worker_count": 0, "active_tasks": 0, "error": str(e)}


async def flush_redis_cache() -> dict[str, Any]:
    try:
        from app.cache import get_cache

        cache = await get_cache()
        if cache and hasattr(cache, "flush"):
            await cache.flush()
            return {"flushed": True}
        return {"flushed": False, "reason": "no cache client"}
    except Exception as e:
        return {"flushed": False, "error": str(e)}

