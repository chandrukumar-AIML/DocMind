from __future__ import annotations

import importlib.util
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Load app/database/base.py DIRECTLY via importlib so we never trigger
# app/database/__init__.py → engine.py → create_engine() → psycopg2/asyncpg.
# Those drivers are not needed for schema introspection and may not be
# installed when alembic runs during a Docker build or CI migration step.
# ---------------------------------------------------------------------------
_base_path = os.path.join(os.path.dirname(__file__), "..", "app", "database", "base.py")
_spec = importlib.util.spec_from_file_location("_app_database_base", _base_path)
_base_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_base_mod)  # type: ignore[union-attr]

Base = _base_mod.Base
metadata = _base_mod.metadata

# Import model modules so their Table definitions are registered on `metadata`.
# We add the repo root to sys.path but we do NOT import via the package so
# __init__.py (and therefore engine.py) is never executed.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def _load_models() -> None:
    for rel in ("app/auth/models.py", "app/documents/models.py"):
        path = os.path.join(os.path.dirname(__file__), "..", rel)
        if not os.path.exists(path):
            continue
        mod_name = rel.replace("/", ".").replace(".py", "")
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        # Inject Base so models can inherit from it
        mod.__dict__["Base"] = Base  # type: ignore[attr-defined]
        sys.modules.setdefault(mod_name, mod)
        try:
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
        except Exception:
            pass  # partial import is fine — tables already registered


_load_models()

target_metadata = metadata

# ---------------------------------------------------------------------------
# Database URL — prefer DATABASE_URL env var; fall back to alembic.ini value.
# Convert asyncpg driver to psycopg2-compatible sync URL for Alembic.
# ---------------------------------------------------------------------------
_db_url = os.getenv("DATABASE_URL", "")
if _db_url:
    _db_url = (
        _db_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres://", "postgresql://")
    )
    config.set_main_option("sqlalchemy.url", _db_url)


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
