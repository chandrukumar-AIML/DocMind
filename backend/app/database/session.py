#           requires a plain async generator function, not a context manager object.
#           Using @asynccontextmanager caused FastAPI to receive a context manager
#           instead of a session, silently breaking ALL async DB injection.
#           no longer exposes .bind on sessionmaker).
"""
Database session dependencies for FastAPI.

Provides both sync and async session generators for dependency injection.

Usage in routes:
    # Async routes (recommended):
    async def my_endpoint(db: AsyncSession = Depends(get_async_db)):
        result = await db.execute(stmt)

    # Sync routes (legacy):
    def my_sync_endpoint(db: Session = Depends(get_db)):
        result = db.execute(stmt)
"""

from __future__ import annotations

import logging
from typing import AsyncGenerator, Generator

from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from .engine import SyncSessionLocal, AsyncSessionLocal, engine, async_engine

logger = logging.getLogger(__name__)


# -- Sync Session Dependency -----------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency: yield sync database session.

    ✅ FIXED: Plain generator function (no @contextmanager decorator).
    FastAPI's Depends() inspects the function signature and calls it directly.
    Wrapping with @contextmanager returns a _GeneratorContextManager object,
    not a generator — FastAPI cannot iterate it as a dependency.

    Usage:
        def my_endpoint(db: Session = Depends(get_db)):
            result = db.execute(stmt)
    """
    db = SyncSessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# -- Async Session Dependency ----------------------------------------------


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency: yield async database session.

    ✅ FIXED: Plain async generator function (no @asynccontextmanager decorator).
    FastAPI's Depends() requires an async generator — a function that contains
    'yield' and returns an AsyncGenerator. Using @asynccontextmanager wraps the
    function into an _AsyncGeneratorContextManager object; FastAPI sees this as
    a callable that returns a context manager, not a session, so db injection
    silently fails (db is a context manager object, not an AsyncSession).

    Usage:
        async def my_endpoint(db: AsyncSession = Depends(get_async_db)):
            result = await db.execute(stmt)
    """
    async with AsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        finally:
            await db.close()


# -- Utility: Raw sessions for scripts/tests -------------------------------


def get_db_session() -> Session:
    """
    Get a raw sync session (caller responsible for commit/close).

    Usage in scripts:
        db = get_db_session()
        try:
            result = db.execute(stmt)
            db.commit()
        finally:
            db.close()
    """
    return SyncSessionLocal()


async def get_async_db_session() -> AsyncSession:
    """
    Get a raw async session (caller responsible for commit/close).

    Usage in async scripts:
        db = await get_async_db_session()
        try:
            result = await db.execute(stmt)
            await db.commit()
        finally:
            await db.close()
    """
    return AsyncSessionLocal()


# -- Health Check ----------------------------------------------------------


async def check_database_health() -> bool:
    """
    Test database connectivity with a simple query.

    Returns:
        True if database is reachable and responsive.
    """
    try:
        async with AsyncSessionLocal() as db:
            from sqlalchemy import text

            await db.execute(text("SELECT 1"))
            return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False


# -- Lifecycle -------------------------------------------------------------


async def close_all_sessions():
    """
    Dispose of all database engine connection pools.
    Call this on application shutdown.

    ✅ FIXED: SQLAlchemy 2.0 sessionmaker no longer has a .bind attribute.
    Dispose engines directly instead of going through the session factory.
    """
    try:
        engine.dispose()
        logger.info("Sync DB engine disposed")
    except Exception as e:
        logger.error(f"Error disposing sync engine: {e}")

    try:
        await async_engine.dispose()
        logger.info("Async DB engine disposed")
    except Exception as e:
        logger.error(f"Error disposing async engine: {e}")


# -- Module Exports --------------------------------------------------------

__all__ = [
    # FastAPI dependencies
    "get_db",
    "get_async_db",
    # Raw sessions for scripts/tests
    "get_db_session",
    "get_async_db_session",
    # Lifecycle
    "check_database_health",
    "close_all_sessions",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.database.session) ----
# ========================================================================

