from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context

# Add repo root to path so we can import app modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Import ONLY the metadata/Base — bypasses engine.py and config.py (avoids pydantic).
# Models must be imported so their tables are registered on the metadata object.
from app.database.base import Base, metadata  # noqa: E402

# Import all models so their Table objects populate `metadata`.
# These imports are order-sensitive: base must come first.
try:
    import app.auth.models  # noqa: F401
    import app.documents.models  # noqa: F401
except Exception:
    pass  # models may not all exist yet during initial migration

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Get DATABASE_URL — prefer env var (set by Render / CI), fall back to alembic.ini.
_db_url = os.getenv("DATABASE_URL")
if _db_url:
    # asyncpg URLs must be sync for Alembic — swap the driver.
    _db_url = _db_url.replace("postgresql+asyncpg://", "postgresql://")
    config.set_main_option("sqlalchemy.url", _db_url)

target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=config.get_main_option("sqlalchemy.url"),
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
