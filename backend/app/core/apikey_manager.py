# backend/app/core/apikey_manager.py
"""
API key management — create, validate, revoke, rotate workspace API keys.
Key format: dmk_{workspace_prefix}_{random_32chars}
Full key shown ONCE — only SHA-256 hash stored.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text

from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_KEY_PREFIX = "dmk"
_KEY_RANDOM_BYTES = 32


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_key(workspace_name: str) -> tuple[str, str, str]:
    """
    Returns (full_key, key_hash, display_prefix).
    full_key = dmk_{ws_slug}_{random}
    display_prefix = first 20 chars of full_key + "…"
    """
    slug = re.sub(r"[^a-z0-9]", "", workspace_name.lower())[:12] or "ws"
    random_part = secrets.token_urlsafe(_KEY_RANDOM_BYTES)[:32]
    full_key = f"{_KEY_PREFIX}_{slug}_{random_part}"
    display_prefix = full_key[:20]
    return full_key, _hash_key(full_key), display_prefix


# ── Schema bootstrap ─────────────────────────────────────────────────────────

async def ensure_apikey_schema() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id  UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                name          VARCHAR(100) NOT NULL,
                key_hash      VARCHAR(255) NOT NULL UNIQUE,
                key_prefix    VARCHAR(20) NOT NULL,
                created_by    UUID REFERENCES users(id) ON DELETE SET NULL,
                last_used_at  TIMESTAMP WITH TIME ZONE,
                usage_count   INTEGER DEFAULT 0,
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                expires_at    TIMESTAMP WITH TIME ZONE,
                scopes        TEXT[] DEFAULT ARRAY['read','write'],
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_api_keys_workspace_id "
            "ON api_keys(workspace_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_api_keys_active "
            "ON api_keys(is_active, workspace_id)"
        ))


# ── Create key ────────────────────────────────────────────────────────────────

async def create_api_key(
    workspace_id: str,
    name: str,
    scopes: list[str],
    created_by: str,
    expires_in_days: Optional[int] = None,
) -> dict[str, Any]:
    """
    Create API key. Returns full key ONCE — never retrievable again.
    """
    # Get workspace name for slug in key
    async with async_engine.connect() as conn:
        ws_row = (await conn.execute(text(
            "SELECT name FROM workspaces WHERE id = :wsid"
        ), {"wsid": workspace_id})).mappings().fetchone()
    ws_name = ws_row["name"] if ws_row else "ws"

    full_key, key_hash, display_prefix = _make_key(ws_name)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days else None
    )

    async with async_engine.begin() as conn:
        row = (await conn.execute(text("""
            INSERT INTO api_keys
                (workspace_id, name, key_hash, key_prefix, created_by, scopes, expires_at)
            VALUES
                (:ws_id, :name, :hash, :prefix, :created_by, :scopes, :expires_at)
            RETURNING id::text AS key_id, name, key_prefix, scopes,
                      expires_at, created_at, is_active
        """), {
            "ws_id": workspace_id, "name": name,
            "hash": key_hash, "prefix": display_prefix,
            "created_by": created_by,
            "scopes": scopes,
            "expires_at": expires_at,
        })).mappings().fetchone()

    return {
        **dict(row),
        "api_key": full_key,       # SHOWN ONCE
        "workspace_id": workspace_id,
        "warning": "Save this key immediately — it will not be shown again.",
    }


# ── Validate key (middleware use) ─────────────────────────────────────────────

async def validate_api_key(raw_key: str) -> dict[str, Any] | None:
    """
    Validate API key and return workspace context.
    Increments usage_count and updates last_used_at.
    Returns None if invalid/inactive/expired.
    """
    if not raw_key.startswith(f"{_KEY_PREFIX}_"):
        return None

    key_hash = _hash_key(raw_key)

    async with async_engine.begin() as conn:
        row = (await conn.execute(text("""
            SELECT ak.id::text AS key_id,
                   ak.workspace_id::text,
                   ak.scopes, ak.is_active, ak.expires_at,
                   w.is_active AS workspace_active
            FROM api_keys ak
            JOIN workspaces w ON w.id = ak.workspace_id
            WHERE ak.key_hash = :hash
        """), {"hash": key_hash})).mappings().fetchone()

        if not row:
            return None
        if not row["is_active"] or not row["workspace_active"]:
            return None
        if row["expires_at"] and datetime.now(timezone.utc) > row["expires_at"]:
            return None

        # Update usage stats
        await conn.execute(text("""
            UPDATE api_keys
            SET last_used_at = NOW(), usage_count = usage_count + 1
            WHERE id = :kid
        """), {"kid": row["key_id"]})

    return {
        "key_id": row["key_id"],
        "workspace_id": row["workspace_id"],
        "scopes": row["scopes"],
    }


# ── List keys ─────────────────────────────────────────────────────────────────

async def list_api_keys(workspace_id: str) -> list[dict[str, Any]]:
    async with async_engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT ak.id::text AS key_id, ak.name, ak.key_prefix, ak.scopes,
                   ak.last_used_at, ak.usage_count, ak.is_active,
                   ak.expires_at, ak.created_at,
                   u.email AS created_by_email
            FROM api_keys ak
            LEFT JOIN users u ON u.id = ak.created_by
            WHERE ak.workspace_id = :wsid
            ORDER BY ak.created_at DESC
        """), {"wsid": workspace_id})).mappings().fetchall()
        return [dict(r) for r in rows]


# ── Revoke key ────────────────────────────────────────────────────────────────

async def revoke_api_key(key_id: str, workspace_id: str) -> None:
    async with async_engine.begin() as conn:
        result = await conn.execute(text("""
            UPDATE api_keys SET is_active = FALSE
            WHERE id = :kid AND workspace_id = :wsid
        """), {"kid": key_id, "wsid": workspace_id})
        if result.rowcount == 0:
            raise ValueError("API key not found or access denied")


# ── Rotate key ────────────────────────────────────────────────────────────────

async def rotate_api_key(
    key_id: str,
    workspace_id: str,
    created_by: str,
) -> dict[str, Any]:
    """Revoke old key, create new key with same name + scopes."""
    async with async_engine.connect() as conn:
        old = (await conn.execute(text("""
            SELECT name, scopes, expires_at FROM api_keys
            WHERE id = :kid AND workspace_id = :wsid AND is_active = TRUE
        """), {"kid": key_id, "wsid": workspace_id})).mappings().fetchone()

    if not old:
        raise ValueError("Active API key not found or access denied")

    await revoke_api_key(key_id, workspace_id)

    expires_in_days = None
    if old["expires_at"]:
        delta = old["expires_at"] - datetime.now(timezone.utc)
        expires_in_days = max(1, delta.days)

    return await create_api_key(
        workspace_id=workspace_id,
        name=old["name"],
        scopes=list(old["scopes"]),
        created_by=created_by,
        expires_in_days=expires_in_days,
    )


# ── Usage history ─────────────────────────────────────────────────────────────

async def get_key_usage(key_id: str, days: int = 30) -> list[dict[str, Any]]:
    async with async_engine.connect() as conn:
        rows = (await conn.execute(text("""
            SELECT DATE(created_at) AS day,
                   COUNT(*) AS request_count
            FROM usage_logs
            WHERE resource_id = :kid
              AND action_type = 'api_key_used'
              AND created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
            ORDER BY day
        """), {"kid": key_id})).mappings().fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    import asyncio

    async def _test():
        print("Testing apikey_manager…")
        full, hsh, prefix = _make_key("lawfirm")
        assert full.startswith("dmk_lawfirm")
        assert len(full) > 30
        assert _hash_key(full) == hsh
        print(f"  Key: {full[:25]}… prefix={prefix} ✓")
        print("Done.")

    asyncio.run(_test())
