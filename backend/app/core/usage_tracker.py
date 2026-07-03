# backend/app/core/usage_tracker.py
"""
Usage tracker — logs workspace actions, enforces plan limits,
provides aggregates for billing.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import text

from app.database.engine import async_engine

logger = logging.getLogger(__name__)

# Action types logged
ACTION_DOCUMENT_UPLOADED = "document_uploaded"
ACTION_OCR_PROCESSED = "ocr_processed"
ACTION_QUERY_EXECUTED = "query_executed"
ACTION_AGENT_QUERY = "agent_query"
ACTION_GRAPH_QUERY = "graph_query"
ACTION_DOCUMENT_DELETED = "document_deleted"
ACTION_API_KEY_USED = "api_key_used"

# Plan limits live in app.core.plan_registry.PLAN_REGISTRY (single source of truth,
# synced onto Workspace.max_docs/max_queries_per_day/max_storage_gb by
# app.core.billing_manager.update_subscription() whenever a plan changes).


# ── Schema bootstrap ─────────────────────────────────────────────────────────


async def ensure_usage_schema() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS usage_logs (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id     UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                user_id          UUID REFERENCES users(id) ON DELETE SET NULL,
                action_type      VARCHAR(50) NOT NULL,
                resource_type    VARCHAR(50),
                resource_id      UUID,
                tokens_used      INTEGER DEFAULT 0,
                ocr_pages        INTEGER DEFAULT 0,
                storage_delta_mb FLOAT DEFAULT 0.0,
                metadata         JSONB DEFAULT '{}',
                created_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_usage_logs_workspace_id " "ON usage_logs(workspace_id)"))
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_usage_logs_ws_date " "ON usage_logs(workspace_id, created_at)")
        )
        # Lazy daily-reset marker for query_count_today (see check_query_limit()) — no
        # Celery Beat schedule exists in this codebase, so resets happen on next check
        # instead of depending on a scheduler.
        await conn.execute(
            text(
                "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
                "query_count_reset_at DATE NOT NULL DEFAULT CURRENT_DATE"
            )
        )


# ── Core log function ─────────────────────────────────────────────────────────


async def log_action(
    workspace_id: str,
    action_type: str,
    user_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    tokens_used: int = 0,
    ocr_pages: int = 0,
    storage_delta_mb: float = 0.0,
    metadata: Optional[dict] = None,
) -> None:
    """Fire-and-forget usage event. Never raises — failures are logged only."""
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO usage_logs
                    (workspace_id, user_id, action_type, resource_type,
                     resource_id, tokens_used, ocr_pages, storage_delta_mb, metadata)
                VALUES
                    (:ws_id, :user_id, :action, :res_type,
                     :res_id, :tokens, :ocr, :storage, CAST(:meta AS jsonb))
            """),
                {
                    "ws_id": workspace_id,
                    "user_id": user_id,
                    "action": action_type,
                    "res_type": resource_type,
                    "res_id": resource_id,
                    "tokens": tokens_used,
                    "ocr": ocr_pages,
                    "storage": storage_delta_mb,
                    "meta": str(metadata or {}).replace("'", '"'),
                },
            )

        # Update workspace running counters
        if action_type == ACTION_DOCUMENT_UPLOADED:
            await _increment_workspace(workspace_id, "doc_count", 1, "storage_used_mb", storage_delta_mb)
        elif action_type == ACTION_DOCUMENT_DELETED:
            await _increment_workspace(workspace_id, "doc_count", -1, "storage_used_mb", -storage_delta_mb)
        elif action_type in (
            ACTION_QUERY_EXECUTED,
            ACTION_AGENT_QUERY,
            ACTION_GRAPH_QUERY,
        ):
            await _increment_workspace(workspace_id, "query_count_today", 1)

    except Exception as e:
        logger.warning(f"[usage_tracker] Failed to log {action_type}: {e}")


async def _increment_workspace(
    workspace_id: str,
    count_col: str,
    count_delta: int,
    storage_col: Optional[str] = None,
    storage_delta: float = 0.0,
) -> None:
    sets = [f"{count_col} = GREATEST(0, {count_col} + :delta)"]
    params: dict[str, Any] = {"wsid": workspace_id, "delta": count_delta}
    if storage_col and storage_delta:
        sets.append(f"{storage_col} = GREATEST(0, {storage_col} + :storage_delta)")
        params["storage_delta"] = storage_delta

    async with async_engine.begin() as conn:
        await conn.execute(text(f"UPDATE workspaces SET {', '.join(sets)} WHERE id = :wsid"), params)


# ── Limit checkers ────────────────────────────────────────────────────────────


async def check_doc_limit(workspace_id: str) -> tuple[bool, str]:
    """Returns (ok, message). ok=True means under limit."""
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT doc_count, max_docs, plan FROM workspaces WHERE id = :wsid
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        return False, "Workspace not found"
    if row["doc_count"] >= row["max_docs"]:
        return False, (
            f"Document limit reached ({row['doc_count']}/{row['max_docs']}). "
            f"Upgrade your plan to add more documents."
        )
    return True, "ok"


async def check_query_limit(workspace_id: str) -> tuple[bool, str]:
    # Lazy reset: if the stored reset marker is before today, zero the counter here
    # instead of depending on a scheduled job (no Celery Beat schedule exists for
    # reset_daily_query_counts() in this codebase — this makes every check self-healing).
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE workspaces
                SET query_count_today = 0, query_count_reset_at = CURRENT_DATE
                WHERE id = :wsid AND query_count_reset_at < CURRENT_DATE
            """),
            {"wsid": workspace_id},
        )

    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT query_count_today, max_queries_per_day, plan
            FROM workspaces WHERE id = :wsid
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        return False, "Workspace not found"
    if row["query_count_today"] >= row["max_queries_per_day"]:
        return False, (
            f"Daily query limit reached ({row['query_count_today']}/"
            f"{row['max_queries_per_day']}). Resets at midnight UTC."
        )
    return True, "ok"


async def check_storage_limit(workspace_id: str, incoming_mb: float) -> tuple[bool, str]:
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT storage_used_mb, max_storage_gb FROM workspaces WHERE id = :wsid
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        return False, "Workspace not found"
    max_mb = row["max_storage_gb"] * 1024
    if (row["storage_used_mb"] + incoming_mb) > max_mb:
        used_gb = row["storage_used_mb"] / 1024
        return False, (
            f"Storage limit reached ({used_gb:.1f}/{row['max_storage_gb']} GB). " f"Upgrade your plan for more storage."
        )
    return True, "ok"


# ── Daily counter reset (call from a scheduled Celery task) ──────────────────


async def reset_daily_query_counts() -> int:
    """Reset query_count_today for all workspaces. Returns rows updated."""
    async with async_engine.begin() as conn:
        result = await conn.execute(text("UPDATE workspaces SET query_count_today = 0 WHERE query_count_today > 0"))
        return result.rowcount


# ── Monthly aggregate ─────────────────────────────────────────────────────────


async def get_workspace_monthly_usage(workspace_id: str, month: str) -> dict[str, Any]:
    year_str, month_str = month.split("-")
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT
                COALESCE(SUM(tokens_used), 0)       AS total_tokens,
                COALESCE(SUM(ocr_pages), 0)         AS total_ocr_pages,
                COALESCE(SUM(storage_delta_mb), 0)  AS storage_added_mb,
                COUNT(CASE WHEN action_type = 'document_uploaded' THEN 1 END) AS docs_uploaded,
                COUNT(CASE WHEN action_type IN
                    ('query_executed','agent_query','graph_query') THEN 1 END) AS total_queries
            FROM usage_logs
            WHERE workspace_id = :wsid
              AND EXTRACT(YEAR FROM created_at) = :year
              AND EXTRACT(MONTH FROM created_at) = :month
        """),
                    {
                        "wsid": workspace_id,
                        "year": int(year_str),
                        "month": int(month_str),
                    },
                )
            )
            .mappings()
            .fetchone()
        )
        return dict(row) if row else {}


if __name__ == "__main__":
    import asyncio

    async def _test():
        print("Testing usage_tracker…")
        ok, msg = await check_doc_limit("00000000-0000-0000-0000-000000000000")
        print(f"  Limit check: ok={ok} msg={msg}")
        print("Done.")

    asyncio.run(_test())
