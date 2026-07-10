"""
Per-workspace LLM provider configuration (BYOK — bring your own key).

Lets a workspace override the platform-wide default LLM provider (set via
OPENAI_API_KEY/OPENAI_BASE_URL/LLM_PROVIDER in app.config.Settings) with their own
provider + model + API key, e.g. so an enterprise customer's queries never touch the
platform's shared OpenAI/Groq account.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.llm_providers import PROVIDER_REGISTRY
from app.database.engine import async_engine

logger = logging.getLogger(__name__)


@dataclass
class WorkspaceLlmConfig:
    workspace_id: str
    provider: str
    model: str
    base_url: Optional[str]
    api_key: str  # decrypted plaintext — only ever held in memory, never logged
    is_active: bool
    updated_at: datetime


async def ensure_workspace_llm_schema() -> None:
    """Create workspace_llm_settings table."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS workspace_llm_settings (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id      UUID NOT NULL UNIQUE REFERENCES workspaces(id) ON DELETE CASCADE,
                provider          VARCHAR(50) NOT NULL,
                model             VARCHAR(100) NOT NULL,
                base_url          VARCHAR(500),
                encrypted_api_key TEXT NOT NULL,
                is_active         BOOLEAN NOT NULL DEFAULT TRUE,
                created_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at        TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workspace_llm_settings_workspace "
                "ON workspace_llm_settings(workspace_id)"
            )
        )
    logger.info("Workspace LLM settings schema verified")


async def get_workspace_llm_config(workspace_id: str) -> Optional[WorkspaceLlmConfig]:
    """Return the active BYOK config for a workspace, or None if not configured."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT provider, model, base_url, encrypted_api_key, is_active, updated_at
                FROM workspace_llm_settings
                WHERE workspace_id = :workspace_id AND is_active = TRUE
            """),
                {"workspace_id": workspace_id},
            )
        ).mappings().first()

    if row is None:
        return None

    try:
        api_key = decrypt_secret(row["encrypted_api_key"])
    except ValueError as e:
        logger.error(f"Failed to decrypt LLM key for workspace {workspace_id}: {e}")
        return None

    return WorkspaceLlmConfig(
        workspace_id=workspace_id,
        provider=row["provider"],
        model=row["model"],
        base_url=row["base_url"],
        api_key=api_key,
        is_active=row["is_active"],
        updated_at=row["updated_at"],
    )


async def get_workspace_llm_config_masked(workspace_id: str) -> Optional[dict]:
    """Return config metadata for display — never the decrypted key, just its last 4 chars."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT provider, model, base_url, encrypted_api_key, is_active, updated_at
                FROM workspace_llm_settings
                WHERE workspace_id = :workspace_id
            """),
                {"workspace_id": workspace_id},
            )
        ).mappings().first()

    if row is None:
        return None

    try:
        api_key = decrypt_secret(row["encrypted_api_key"])
        masked = f"****{api_key[-4:]}" if len(api_key) >= 4 else "****"
    except ValueError:
        masked = "****(undecryptable)"

    return {
        "provider": row["provider"],
        "model": row["model"],
        "base_url": row["base_url"],
        "api_key_masked": masked,
        "is_active": row["is_active"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def upsert_workspace_llm_config(
    workspace_id: str,
    provider: str,
    api_key: str,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict:
    """Create or replace a workspace's BYOK LLM config. Returns masked metadata."""
    if provider not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown provider '{provider}'. Supported: {list(PROVIDER_REGISTRY)}")

    registry_entry = PROVIDER_REGISTRY[provider]
    resolved_model = model or registry_entry["default_model"]
    resolved_base_url = base_url if base_url is not None else registry_entry["base_url"]
    encrypted = encrypt_secret(api_key)

    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO workspace_llm_settings
                (workspace_id, provider, model, base_url, encrypted_api_key, is_active, updated_at)
            VALUES
                (:workspace_id, :provider, :model, :base_url, :encrypted_api_key, TRUE, NOW())
            ON CONFLICT (workspace_id) DO UPDATE SET
                provider = EXCLUDED.provider,
                model = EXCLUDED.model,
                base_url = EXCLUDED.base_url,
                encrypted_api_key = EXCLUDED.encrypted_api_key,
                is_active = TRUE,
                updated_at = NOW()
        """),
            {
                "workspace_id": workspace_id,
                "provider": provider,
                "model": resolved_model,
                "base_url": resolved_base_url,
                "encrypted_api_key": encrypted,
            },
        )

    logger.info(f"Workspace {workspace_id} LLM config set to provider={provider}, model={resolved_model}")
    return await get_workspace_llm_config_masked(workspace_id)


async def delete_workspace_llm_config(workspace_id: str) -> bool:
    """Revert a workspace to the platform default. Returns True if a row was deleted."""
    async with async_engine.begin() as conn:
        result = await conn.execute(
            text("DELETE FROM workspace_llm_settings WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Workspace {workspace_id} LLM config removed — reverted to platform default")
    return deleted


__all__ = [
    "WorkspaceLlmConfig",
    "ensure_workspace_llm_schema",
    "get_workspace_llm_config",
    "get_workspace_llm_config_masked",
    "upsert_workspace_llm_config",
    "delete_workspace_llm_config",
]
