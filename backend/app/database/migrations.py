"""
Schema repair utilities — idempotent helpers that add columns/indexes to existing
tables that were created before Alembic tracked them.

These functions are called during startup lifespan ONLY for backwards compatibility
with existing deployments. All NEW schema changes must go through Alembic revisions
in alembic/versions/. Do not add new ensure_* functions here — write a migration.

Usage:
    from app.database.migrations import apply_pending_repairs
    await apply_pending_repairs()
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import text, inspect

from app.database.engine import async_engine

logger = logging.getLogger(__name__)


class ColumnRepair(NamedTuple):
    table: str
    column: str
    definition: str
    add_index: bool = False


class EnumValueRepair(NamedTuple):
    enum_name: str
    value: str


# All pending column repairs in one place. When a column is confirmed to exist
# in the initial migration (0001_initial_schema.py), remove it from this list.
_COLUMN_REPAIRS: list[ColumnRepair] = [
    # users table
    ColumnRepair("users", "display_name", "VARCHAR(100)"),
    ColumnRepair("users", "updated_at", "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP", add_index=True),
    # answers table
    ColumnRepair("answers", "updated_at", "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"),
    ColumnRepair("answers", "correlation_id", "VARCHAR(128)", add_index=True),
    # citations table
    ColumnRepair("citations", "updated_at", "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP"),
    ColumnRepair("citations", "correlation_id", "VARCHAR(128)", add_index=True),
    # workspaces — billing (Stripe)
    ColumnRepair("workspaces", "stripe_customer_id", "VARCHAR(255)"),
    ColumnRepair("workspaces", "stripe_subscription_id", "VARCHAR(255)"),
    ColumnRepair("workspaces", "subscription_status", "VARCHAR(30) NOT NULL DEFAULT 'none'"),
    # workspaces — SSO
    ColumnRepair("workspaces", "sso_provider", "VARCHAR(50)"),
    ColumnRepair("workspaces", "sso_config", "JSONB"),
    # workspaces — LLM config
    ColumnRepair("workspaces", "llm_config", "JSONB"),
    # soft-delete — workspaces
    ColumnRepair("workspaces", "deleted_at", "TIMESTAMP WITH TIME ZONE", add_index=True),
    ColumnRepair("workspaces", "is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
    # soft-delete — users
    ColumnRepair("users", "deleted_at", "TIMESTAMP WITH TIME ZONE", add_index=True),
    ColumnRepair("users", "is_deleted", "BOOLEAN NOT NULL DEFAULT FALSE"),
]

_ENUM_REPAIRS: list[EnumValueRepair] = [
    EnumValueRepair("user_role_enum", "workspace_admin"),
]


async def apply_pending_repairs() -> None:
    """
    Run all column and enum repairs idempotently.

    Safe to call on every startup — operations are no-ops when columns already exist.
    PostgreSQL-only; silently skips on other dialects.
    """
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            logger.warning(f"Schema repairs are PostgreSQL-only; skipping for dialect: {conn.dialect.name}")
            return

        def _get_columns(sync_conn, table: str) -> set[str]:
            inspector = inspect(sync_conn)
            try:
                return {col["name"] for col in inspector.get_columns(table, schema="public")}
            except Exception:
                return set()

        def _get_indexes(sync_conn, table: str) -> set[str]:
            inspector = inspect(sync_conn)
            try:
                return {idx["name"] for idx in inspector.get_indexes(table, schema="public")}
            except Exception:
                return set()

        for repair in _COLUMN_REPAIRS:
            existing = await conn.run_sync(_get_columns, repair.table)
            if repair.column in existing:
                continue

            logger.info(f"Adding column: {repair.table}.{repair.column}")
            await conn.execute(
                text(f"ALTER TABLE {repair.table} ADD COLUMN IF NOT EXISTS {repair.column} {repair.definition}")
            )

            if repair.add_index:
                index_name = f"ix_{repair.table}_{repair.column}"
                existing_idx = await conn.run_sync(_get_indexes, repair.table)
                if index_name not in existing_idx:
                    await conn.execute(
                        text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {repair.table} ({repair.column})")
                    )

        for enum_repair in _ENUM_REPAIRS:
            await conn.execute(
                text(f"ALTER TYPE {enum_repair.enum_name} ADD VALUE IF NOT EXISTS '{enum_repair.value}'")
            )

    logger.info("Schema repairs complete")
