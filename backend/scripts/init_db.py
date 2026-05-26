#!/usr/bin/env python3
"""Create all database tables from SQLAlchemy models (idempotent)."""
import sys, os, asyncio, uuid
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Import ALL models so their metadata is registered
from app.database.base import metadata
from app.auth.models import User, Workspace, WorkspaceMember, UserRole  # noqa: F401
from app.config import get_settings

async def init_db():
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)

    async with engine.begin() as conn:
        print("Creating all tables from SQLAlchemy models...")

        # Create PostgreSQL enum type first (idempotent)
        await conn.execute(text(
            "DO $$ BEGIN "
            "  CREATE TYPE user_role_enum AS ENUM ('admin', 'editor', 'viewer'); "
            "EXCEPTION WHEN duplicate_object THEN NULL; "
            "END $$;"
        ))
        print("  [ok] user_role_enum")

        # Create all tables from metadata
        await conn.run_sync(metadata.create_all)
        print("  [ok] all tables created (idempotent)")

        # Verify core tables
        result = await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        ))
        tables = [r[0] for r in result.fetchall()]
        print(f"  [ok] tables in DB: {', '.join(tables)}")

    await engine.dispose()
    print("\nDatabase initialized successfully!")

if __name__ == "__main__":
    asyncio.run(init_db())
