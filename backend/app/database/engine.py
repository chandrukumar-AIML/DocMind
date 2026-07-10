
from __future__ import annotations

import logging
from typing import Final, Optional
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, event, pool, text, Engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session

from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash

logger = logging.getLogger(__name__)


# -- Helper: Sanitize URL for logging (mask password) ------------------
def _sanitize_url(url: str) -> str:
    """Mask password in database URL for safe logging."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            # Replace password with ***
            netloc = f"{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
        return url
    except Exception:
        return "***SANITIZED***"


# -- Helper: Convert async URL to sync URL -----------------------------
def _async_to_sync_url(async_url: str) -> str:
    """Convert async driver URL to sync equivalent."""
    replacements = {
        "postgresql+asyncpg://": "postgresql://",
        "postgres+asyncpg://": "postgres://",
        "mysql+aiomysql://": "mysql+pymysql://",
        "sqlite+aiosqlite://": "sqlite://",
    }
    for async_drv, sync_drv in replacements.items():
        if async_url.startswith(async_drv):
            return async_url.replace(async_drv, sync_drv, 1)
    return async_url


# -- Configuration -----------------------------------------------------
_sync_url = _async_to_sync_url(settings.database_url)
_async_url = settings.database_url

# Pool sizing: async pool smaller because async requests are non-blocking
# and spend less time holding connections
_sync_pool_size = getattr(settings, "db_pool_size", 20)
_sync_max_overflow = getattr(settings, "db_max_overflow", 40)
_async_pool_size = max(5, _sync_pool_size // 2)  # ✅ MIN 5 to avoid starvation
_async_max_overflow = max(10, _sync_max_overflow // 2)

# Connection timeouts (seconds)
_CONNECT_TIMEOUT = getattr(settings, "db_connect_timeout", 10)
_STATEMENT_TIMEOUT = getattr(settings, "db_statement_timeout", 30)

# -- Sync Engine -------------------------------------------------------
engine: Final[Engine] = create_engine(
    _sync_url,
    poolclass=pool.QueuePool,
    pool_size=_sync_pool_size,
    max_overflow=_sync_max_overflow,
    pool_pre_ping=True,  # ✅ Verify connection alive before use
    pool_recycle=3600,  # ✅ Recycle connections hourly (avoid DB-side timeout)
    connect_args={
        "connect_timeout": _CONNECT_TIMEOUT,
        "options": f"-c statement_timeout={_STATEMENT_TIMEOUT * 1000}",  # PostgreSQL-specific
    },
    echo=False,
    future=True,
)

# -- Async Engine ------------------------------------------------------
async_engine: Final[AsyncEngine] = create_async_engine(
    _async_url,
    pool_size=_async_pool_size,
    max_overflow=_async_max_overflow,
    pool_pre_ping=True,
    pool_recycle=3600,
    connect_args={
        "timeout": _CONNECT_TIMEOUT,  # asyncpg-specific
        "command_timeout": _STATEMENT_TIMEOUT,  # asyncpg-specific
    },
    execution_options={
        "statement_timeout": _STATEMENT_TIMEOUT * 1000,  # PostgreSQL via asyncpg
    },
    echo=False,
    future=True,
)

# -- Session Factories -------------------------------------------------
SyncSessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    expire_on_commit=False,
    autoflush=False,
)

AsyncSessionLocal = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# -- EXPORTED: Session getters -----------------------------------------
def get_sync_session() -> Session:
    """Get a new sync session (caller manages lifecycle)."""
    return SyncSessionLocal()


async def get_async_session() -> AsyncSession:
    """Get a new async session (caller manages lifecycle)."""
    return AsyncSessionLocal()


# -- Legacy Compatibility Aliases --------------------------------------
get_db = get_sync_session


# -- Connection Event Logging (SANITIZED) ------------------------------
@event.listens_for(engine, "connect")
def on_connect(dbapi_conn, record):
    if logger.isEnabledFor(logging.DEBUG):
        safe_url = _sanitize_url(record.url if hasattr(record, "url") else str(record))
        logger.debug(f"Sync DB connected: {safe_url}")


@event.listens_for(async_engine.sync_engine, "connect")
def on_async_connect(dbapi_conn, record):
    if logger.isEnabledFor(logging.DEBUG):
        safe_url = _sanitize_url(record.url if hasattr(record, "url") else str(record))
        logger.debug(f"Async DB connected: {safe_url}")


# -- Health Check (with retry + optional schema verify) ----------------
async def check_database_health(verify_schema: bool = False) -> bool:
    """
    Test database connectivity.

    Args:
        verify_schema: If True, also check that core tables exist

    Returns:
        True if healthy, False otherwise
    """
    retries = 3
    last_error: Optional[Exception] = None

    for attempt in range(retries):
        try:
            async with async_engine.connect() as conn:
                # Basic connectivity
                await conn.execute(text("SELECT 1"))

                # Optional: verify core tables exist
                if verify_schema:
                    result = await conn.execute(
                        text("""
                            SELECT EXISTS (
                                SELECT FROM information_schema.tables 
                                WHERE table_schema = 'public' 
                                AND table_name = 'users'
                            );
                        """)
                    )
                    if not result.scalar():
                        logger.warning("DB health: 'users' table not found")
                        return False

                return True

        except Exception as e:
            last_error = e
            logger.warning(f"DB health check attempt {attempt + 1}/{retries} failed: {e}")
            if attempt < retries - 1:
                import asyncio

                await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
            continue

    logger.error(f"DB health check failed after {retries} attempts: {last_error}")
    return False


# -- Graceful Shutdown -------------------------------------------------
async def dispose_engines() -> None:
    """
    Dispose of all database engines for clean shutdown.

    Call this during application shutdown to release pooled connections.
    """
    try:
        # Dispose sync engine
        if engine:
            engine.dispose()
            logger.debug("Sync engine disposed")

        # Dispose async engine
        if async_engine:
            await async_engine.dispose()
            logger.debug("Async engine disposed")

    except Exception as e:
        logger.warning(f"Error during engine disposal: {e}")


# -- Module Exports ----------------------------------------------------
__all__ = [
    "engine",
    "async_engine",
    "SyncSessionLocal",
    "AsyncSessionLocal",
    "get_sync_session",
    "get_async_session",
    "get_db",
    "check_database_health",
    "dispose_engines",  # ✅ NEW: For graceful shutdown
    "_sanitize_url",  # ✅ Exported for testing
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.database.engine) -----
# ========================================================================

