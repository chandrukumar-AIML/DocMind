"""Small idempotent schema repairs for local/dev databases.

The project currently uses SQLAlchemy ``create_all`` rather than migrations.
``create_all`` does not add columns to an existing table, so this keeps older
local databases compatible with the current auth model without dropping data.

✅ FIXED:
- updated_at now has DEFAULT + server_default for auto-timestamp
- Optional index on updated_at for query performance
- Dialect check for PostgreSQL-specific syntax
- Extensible pattern for future column additions
"""

from __future__ import annotations

import logging

from sqlalchemy import text, inspect

from app.database.engine import async_engine

logger = logging.getLogger(__name__)


async def ensure_auth_schema() -> None:
    """
    Ensure columns required by auth routes exist on the users table.

    Idempotent: safe to run multiple times.
    PostgreSQL-specific: uses IF NOT EXISTS syntax.
    """
    async with async_engine.begin() as conn:
        # ✅ FIXED: Check dialect before using PostgreSQL-specific syntax
        dialect = conn.dialect.name
        if dialect != "postgresql":
            logger.warning(f"Schema repairs are PostgreSQL-specific; detected dialect: {dialect}")
            return

        # Define columns to ensure (extensible pattern)
        columns_to_add = [
            {
                "name": "display_name",
                "definition": "VARCHAR(100)",
                "description": "User display name for UI",
            },
            {
                "name": "updated_at",
                # ✅ FIXED: Add DEFAULT + server_default for auto-timestamp on INSERT
                "definition": "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
                "description": "Last update timestamp (auto-set on INSERT)",
                # Optional: Add index for sorting/filtering performance
                "index": True,
            },
        ]

        # FIXED: SQLAlchemy inspection performs sync IO; run it through
        # AsyncConnection.run_sync so greenlet_spawn is active.
        def _get_existing_columns(sync_conn):
            inspector = inspect(sync_conn)
            return {col["name"] for col in inspector.get_columns("users", schema="public")}

        def _get_existing_indexes(sync_conn):
            inspector = inspect(sync_conn)
            return {idx["name"] for idx in inspector.get_indexes("users", schema="public")}

        existing_columns = await conn.run_sync(_get_existing_columns)
        existing_indexes = await conn.run_sync(_get_existing_indexes)

        for col in columns_to_add:
            col_name = col["name"]
            col_def = col["definition"]

            if col_name in existing_columns:
                logger.debug(f"Column 'users.{col_name}' already exists")
                continue

            # ✅ FIXED: Log what we're adding
            logger.info(f"Adding column 'users.{col_name}': {col['description']}")

            stmt = text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_def}")
            await conn.execute(stmt)

            # ✅ FIXED: Create index if specified (for updated_at sorting)
            if col.get("index"):
                index_name = f"ix_users_{col_name}"
                if index_name not in existing_indexes:
                    logger.info(f"Creating index '{index_name}' on users.{col_name}")
                    # ✅ FIXED: Use CREATE INDEX IF NOT EXISTS (not CONCURRENTLY inside transaction)
                    await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON users ({col_name})"))
                else:
                    logger.debug(f"Index '{index_name}' already exists")

        # Repair: user_role_enum was created without 'workspace_admin' in some
        # environments (the value was added to the Python enum — app.auth.models.UserRole
        # — and used by SSO JIT-provisioning, superadmin, and self-serve registration,
        # but the DB enum type itself was never migrated). Safe to run every startup —
        # ADD VALUE IF NOT EXISTS is a no-op once the value exists.
        await conn.execute(text("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'workspace_admin'"))

    logger.info("Auth database schema verified and repaired if needed")


async def ensure_workspace_schema() -> None:
    """
    Example: Extend this pattern for workspace table repairs.

    Add columns like:
    - max_documents, max_queries (if missing)
    - soft-delete columns (deleted_at, is_deleted)
    """
    # Future implementation:
    # columns_to_add = [
    #     {"name": "deleted_at", "definition": "TIMESTAMP WITH TIME ZONE", "index": True},
    #     {"name": "is_deleted", "definition": "BOOLEAN DEFAULT FALSE"},
    # ]
    # ... same pattern as ensure_auth_schema()
    pass


async def ensure_provenance_schema() -> None:
    """Ensure provenance tables match the current SQLAlchemy models."""
    async with async_engine.begin() as conn:
        dialect = conn.dialect.name
        if dialect != "postgresql":
            logger.warning(f"Schema repairs are PostgreSQL-specific; detected dialect: {dialect}")
            return

        table_repairs = {
            "answers": [
                {
                    "name": "updated_at",
                    "definition": "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
                    "index": False,
                },
                {
                    "name": "correlation_id",
                    "definition": "VARCHAR(128)",
                    "index": True,
                },
            ],
            "citations": [
                {
                    "name": "updated_at",
                    "definition": "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
                    "index": False,
                },
                {
                    "name": "correlation_id",
                    "definition": "VARCHAR(128)",
                    "index": True,
                },
            ],
            "document_store": [
                {
                    "name": "updated_at",
                    "definition": "TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP",
                    "index": False,
                },
                {
                    "name": "correlation_id",
                    "definition": "VARCHAR(128)",
                    "index": True,
                },
            ],
        }

        def _inspect_table(sync_conn, table_name: str):
            inspector = inspect(sync_conn)
            if not inspector.has_table(table_name, schema="public"):
                return None, None
            columns = {col["name"] for col in inspector.get_columns(table_name, schema="public")}
            indexes = {idx["name"] for idx in inspector.get_indexes(table_name, schema="public")}
            return columns, indexes

        for table_name, columns_to_add in table_repairs.items():
            existing_columns, existing_indexes = await conn.run_sync(_inspect_table, table_name)
            if existing_columns is None:
                logger.debug(f"Table '{table_name}' does not exist yet; skipping provenance repair")
                continue

            for col in columns_to_add:
                col_name = col["name"]
                if col_name not in existing_columns:
                    logger.info(f"Adding column '{table_name}.{col_name}'")
                    await conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col['definition']}")
                    )

                if col.get("index"):
                    index_name = f"ix_{table_name}_{col_name}"
                    if index_name not in existing_indexes:
                        logger.info(f"Creating index '{index_name}' on {table_name}.{col_name}")
                        await conn.execute(
                            text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({col_name})")
                        )

    logger.info("Provenance database schema verified and repaired if needed")


# -- Module Exports ----------------------------------------------------
__all__ = ["ensure_auth_schema", "ensure_workspace_schema", "ensure_provenance_schema"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
