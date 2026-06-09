# backend/app/database/session.py
# DVMELTSS-FIX: M - Modular, S - Separation, E - Error handling
# ASCALE-FIX: A - Async, S - Separation
# ✅ FIXED: Removed @asynccontextmanager from get_async_db — FastAPI Depends()
#           requires a plain async generator function, not a context manager object.
#           Using @asynccontextmanager caused FastAPI to receive a context manager
#           instead of a session, silently breaking ALL async DB injection.
# ✅ FIXED: Removed @contextmanager from get_db for the same reason.
# ✅ FIXED: close_all_sessions uses engine dispose() directly (SQLAlchemy 2.0
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

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    async def run_tests():
        print("🔍 Testing Database Session module (app/database/session.py)")
        print("=" * 70)

        try:
            from app.database.session import (
                get_db,
                get_async_db,
                get_db_session,
                get_async_db_session,
                check_database_health,
                close_all_sessions,
            )
            from sqlalchemy.orm import Session
            from sqlalchemy.ext.asyncio import AsyncSession
            import inspect

            # -- Test 1: Function signatures -----------------------------
            print("\n📌 Test 1: Function signatures (FastAPI compatible)")

            # get_db should be a generator function (not decorated)
            assert inspect.isgeneratorfunction(get_db), "get_db should be a generator"
            print("   ✅ get_db: generator function (FastAPI compatible)")

            # get_async_db should be an async generator function
            assert inspect.isasyncgenfunction(get_async_db), "get_async_db should be async generator"
            print("   ✅ get_async_db: async generator function (FastAPI compatible)")

            # -- Test 2: Raw session getters (mocked) --------------------
            print("\n📌 Test 2: Raw session getters (mocked creation)")

            with patch("app.database.session.SyncSessionLocal") as mock_sync, patch(
                "app.database.session.AsyncSessionLocal"
            ) as mock_async:
                mock_sync.return_value = MagicMock(spec=Session)
                mock_async.return_value = AsyncMock(spec=AsyncSession)

                # Test sync raw getter
                sync_sess = get_db_session()
                assert isinstance(sync_sess, MagicMock)
                print("   ✅ get_db_session() returns session")

                # Test async raw getter
                async_sess = await get_async_db_session()
                assert isinstance(async_sess, AsyncMock)
                print("   ✅ get_async_db_session() returns async session")

            # -- Test 3: Dependency generators (mocked lifecycle) --------
            print("\n📌 Test 3: Dependency generators (mocked lifecycle)")

            # Mock the session factories
            with patch("app.database.session.SyncSessionLocal") as mock_sync, patch(
                "app.database.session.AsyncSessionLocal"
            ) as mock_async:
                # Setup sync session mock
                mock_sync_sess = MagicMock(spec=Session)
                mock_sync.return_value = mock_sync_sess

                # Test sync generator: success path
                gen = get_db()
                sess = next(gen)  # Should yield session
                assert sess is mock_sync_sess
                # ✅ FIX: Catch StopIteration as normal generator completion
                try:
                    gen.send(None)  # Should commit and close, then raise StopIteration
                except StopIteration:
                    pass  # Expected: generator finished
                mock_sync_sess.commit.assert_called_once()
                mock_sync_sess.close.assert_called_once()
                print("   ✅ get_db: success path (commit + close)")

                # Test sync generator: error path (rollback)
                mock_sync_sess.reset_mock()
                gen = get_db()
                sess = next(gen)
                try:
                    gen.throw(RuntimeError("Test error"))
                except RuntimeError:
                    pass  # Expected: error propagated
                except StopIteration:
                    pass  # Also acceptable: generator finished after error
                mock_sync_sess.rollback.assert_called_once()
                mock_sync_sess.close.assert_called_once()
                print("   ✅ get_db: error path (rollback + close)")

                # Setup async session mock
                mock_async_sess = AsyncMock(spec=AsyncSession)
                mock_async.return_value.__aenter__.return_value = mock_async_sess

                # Test async generator: success path
                async_gen = get_async_db()
                sess = await async_gen.asend(None)  # Should yield session
                assert sess is mock_async_sess
                # ✅ FIX: Catch StopAsyncIteration as normal async generator completion
                try:
                    await async_gen.asend(None)  # Should commit and close
                except StopAsyncIteration:
                    pass  # Expected: async generator finished
                mock_async_sess.commit.assert_called_once()
                mock_async_sess.close.assert_called_once()
                print("   ✅ get_async_db: success path (commit + close)")

                # Test async generator: error path (rollback)
                mock_async_sess.reset_mock()
                async_gen = get_async_db()
                sess = await async_gen.asend(None)
                try:
                    await async_gen.athrow(RuntimeError("Test error"))
                except RuntimeError:
                    pass  # Expected: error propagated
                except StopAsyncIteration:
                    pass  # Also acceptable: async generator finished after error
                mock_async_sess.rollback.assert_called_once()
                mock_async_sess.close.assert_called_once()
                print("   ✅ get_async_db: error path (rollback + close)")

            # -- Test 4: Health check (mocked) --------------------------
            print("\n📌 Test 4: check_database_health (mocked)")

            with patch("app.database.session.AsyncSessionLocal") as mock_async_local:
                mock_sess = AsyncMock()
                mock_async_local.return_value.__aenter__.return_value = mock_sess
                mock_sess.execute = AsyncMock()

                # Healthy DB
                healthy = await check_database_health()
                assert healthy is True
                print("   ✅ Health check: passed when DB reachable")

                # Unhealthy DB (exception)
                mock_sess.execute.side_effect = Exception("Connection failed")
                healthy = await check_database_health()
                assert healthy is False
                print("   ✅ Health check: failed when DB unreachable")

            # -- Test 5: Lifecycle shutdown (mocked) --------------------
            print("\n📌 Test 5: close_all_sessions (graceful shutdown)")

            with patch("app.database.session.engine") as mock_sync_engine, patch(
                "app.database.session.async_engine"
            ) as mock_async_engine:
                mock_async_engine.dispose = AsyncMock()

                await close_all_sessions()

                mock_sync_engine.dispose.assert_called_once()
                mock_async_engine.dispose.assert_called_once()
                print("   ✅ close_all_sessions: disposed both engines")

            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Database Session module verified.")
            print("\n💡 What we verified:")
            print("   • Signatures: get_db/get_async_db are proper generators ✅")
            print("   • Lifecycle: Sessions commit on success, rollback on error ✅")
            print("   • Cleanup: Sessions always close in finally block ✅")
            print("   • Health: Connectivity check with exception handling ✅")
            print("   • Shutdown: Engine disposal for graceful shutdown ✅")
            print("\n🔐 Production: Proper session lifecycle prevents connection leaks")
            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    # Run async tests
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
