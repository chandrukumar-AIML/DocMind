"""
Per-workspace SSO (OIDC) configuration.

Lets a workspace configure its own OIDC identity provider (Okta, Azure AD/Entra ID,
Google Workspace, Auth0, OneLogin — any standards-compliant OIDC issuer) so its users can
log in via SSO alongside the existing password login. See app/api/routes/sso.py for the
authorize/callback flow that uses this config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from app.core.crypto import decrypt_secret, encrypt_secret
from app.database.engine import async_engine

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceSsoConfig:
    workspace_id: str
    client_id: str
    client_secret: str  # decrypted plaintext — only ever held in memory, never logged
    issuer: str
    is_active: bool
    updated_at: datetime


async def ensure_workspace_sso_schema() -> None:
    """Create workspace_sso_settings table and repair users.hashed_password nullability."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS workspace_sso_settings (
                id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id             UUID NOT NULL UNIQUE REFERENCES workspaces(id) ON DELETE CASCADE,
                client_id                VARCHAR(255) NOT NULL,
                encrypted_client_secret  TEXT NOT NULL,
                issuer                   VARCHAR(500) NOT NULL,
                is_active                BOOLEAN NOT NULL DEFAULT TRUE,
                created_at               TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at               TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workspace_sso_settings_workspace "
                "ON workspace_sso_settings(workspace_id)"
            )
        )
        # SSO-only users are JIT-provisioned with no password (see app/api/routes/sso.py) —
        # the users table predates SSO and has hashed_password NOT NULL from registration.
        await conn.execute(text("ALTER TABLE users ALTER COLUMN hashed_password DROP NOT NULL"))
    logger.info("Workspace SSO settings schema verified")


async def get_workspace_sso_config(workspace_id: str) -> Optional[WorkspaceSsoConfig]:
    """Return the active SSO config for a workspace, or None if not configured."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT client_id, encrypted_client_secret, issuer, is_active, updated_at
                FROM workspace_sso_settings
                WHERE workspace_id = :workspace_id AND is_active = TRUE
            """),
                {"workspace_id": workspace_id},
            )
        ).mappings().first()

    return _row_to_config(workspace_id, row)


async def get_workspace_sso_config_by_slug(workspace_slug: str) -> Optional[WorkspaceSsoConfig]:
    """Look up SSO config by workspace slug — used by /sso/authorize, called before login."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT s.workspace_id, s.client_id, s.encrypted_client_secret, s.issuer,
                       s.is_active, s.updated_at
                FROM workspace_sso_settings s
                JOIN workspaces w ON w.id = s.workspace_id
                WHERE w.slug = :slug AND s.is_active = TRUE AND w.is_active = TRUE
            """),
                {"slug": workspace_slug},
            )
        ).mappings().first()

    if row is None:
        return None
    return _row_to_config(str(row["workspace_id"]), row)


def _row_to_config(workspace_id: str, row) -> Optional[WorkspaceSsoConfig]:
    if row is None:
        return None
    try:
        client_secret = decrypt_secret(row["encrypted_client_secret"])
    except ValueError as e:
        logger.error(f"Failed to decrypt SSO client secret for workspace {workspace_id}: {e}")
        return None

    return WorkspaceSsoConfig(
        workspace_id=workspace_id,
        client_id=row["client_id"],
        client_secret=client_secret,
        issuer=row["issuer"],
        is_active=row["is_active"],
        updated_at=row["updated_at"],
    )


async def get_workspace_sso_config_masked(workspace_id: str) -> Optional[dict]:
    """Return config metadata for display — never the decrypted secret, just its last 4 chars."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT client_id, encrypted_client_secret, issuer, is_active, updated_at
                FROM workspace_sso_settings
                WHERE workspace_id = :workspace_id
            """),
                {"workspace_id": workspace_id},
            )
        ).mappings().first()

    if row is None:
        return None

    try:
        secret = decrypt_secret(row["encrypted_client_secret"])
        masked = f"****{secret[-4:]}" if len(secret) >= 4 else "****"
    except ValueError:
        masked = "****(undecryptable)"

    return {
        "client_id": row["client_id"],
        "client_secret_masked": masked,
        "issuer": row["issuer"],
        "is_active": row["is_active"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def upsert_workspace_sso_config(
    workspace_id: str,
    client_id: str,
    client_secret: str,
    issuer: str,
) -> dict:
    """Create or replace a workspace's SSO config. Returns masked metadata."""
    encrypted = encrypt_secret(client_secret)

    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO workspace_sso_settings
                (workspace_id, client_id, encrypted_client_secret, issuer, is_active, updated_at)
            VALUES
                (:workspace_id, :client_id, :encrypted_client_secret, :issuer, TRUE, NOW())
            ON CONFLICT (workspace_id) DO UPDATE SET
                client_id = EXCLUDED.client_id,
                encrypted_client_secret = EXCLUDED.encrypted_client_secret,
                issuer = EXCLUDED.issuer,
                is_active = TRUE,
                updated_at = NOW()
        """),
            {
                "workspace_id": workspace_id,
                "client_id": client_id,
                "encrypted_client_secret": encrypted,
                "issuer": issuer,
            },
        )

    logger.info(f"Workspace {workspace_id} SSO config set: issuer={issuer}")
    return await get_workspace_sso_config_masked(workspace_id)


async def delete_workspace_sso_config(workspace_id: str) -> bool:
    """Remove a workspace's SSO config. Returns True if a row was deleted."""
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM workspace_sso_settings WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Workspace {workspace_id} SSO config removed")
    return deleted


__all__ = [
    "WorkspaceSsoConfig",
    "ensure_workspace_sso_schema",
    "get_workspace_sso_config",
    "get_workspace_sso_config_by_slug",
    "get_workspace_sso_config_masked",
    "upsert_workspace_sso_config",
    "delete_workspace_sso_config",
]
