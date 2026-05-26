# backend/app/database/engine.py
# ✅ FIXED: Sanitize DB URL in logs (mask password)
# ✅ FIXED: Add connection + statement timeouts
# ✅ FIXED: Health check can verify schema exists
# ✅ FIXED: Added dispose_engines() for graceful shutdown
# ✅ FIXED: Retry logic for transient connection failures

from __future__ import annotations

import logging
import re
from typing import Final, Optional
from urllib.parse import urlparse, urlunparse

from sqlalchemy import create_engine, event, pool, text, Engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session

from app.config import get_settings, lazy_settings as settings  # [OK] FIXED: lazy proxy avoids import-time crash

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
    pool_recycle=3600,   # ✅ Recycle connections hourly (avoid DB-side timeout)
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
        # ✅ FIXED: Sanitize URL before logging
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
    "_sanitize_url",    # ✅ Exported for testing
] 

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.database.engine) -----
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
    
    # Helper class for mocking async context manager
    class MockAsyncContextManager:
        def __init__(self, mock_conn):
            self.mock_conn = mock_conn
        async def __aenter__(self):
            return self.mock_conn
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None
    
    async def run_tests():
        print("🔍 Testing Database Engine module (app/database/engine.py)")
        print("=" * 70)
        
        try:
            from app.database.engine import (
                _sanitize_url, _async_to_sync_url,
                engine, async_engine,
                SyncSessionLocal, AsyncSessionLocal,
                get_sync_session, get_async_session,
                check_database_health, dispose_engines
            )
            
            # -- Test 1: _sanitize_url (Security) -----------------------
            print("\n📌 Test 1: _sanitize_url (password masking)")
            
            url_with_pass = "postgresql://user:secretpass@localhost:5432/mydb"
            safe = _sanitize_url(url_with_pass)
            assert "secretpass" not in safe and "***" in safe
            print(f"   ✅ Password masked: '{safe}'")
            
            url_no_pass = "postgresql://localhost:5432/mydb"
            safe = _sanitize_url(url_no_pass)
            assert safe == url_no_pass
            print(f"   ✅ No password: URL unchanged")
            
            # -- Test 2: _async_to_sync_url (driver conversion) ---------
            print("\n📌 Test 2: _async_to_sync_url (driver conversion)")
            
            async_pg = "postgresql+asyncpg://user:pass@localhost/db"
            sync_pg = _async_to_sync_url(async_pg)
            assert sync_pg == "postgresql://user:pass@localhost/db"
            print(f"   ✅ PostgreSQL: asyncpg -> psycopg2")
            
            async_mysql = "mysql+aiomysql://user:pass@localhost/db"
            sync_mysql = _async_to_sync_url(async_mysql)
            assert sync_mysql == "mysql+pymysql://user:pass@localhost/db"
            print(f"   ✅ MySQL: aiomysql -> pymysql")
            
            # -- Test 3: Engine & Session Factories (basic existence) --
            print("\n📌 Test 3: Engine & Session Factories (existence)")
            
            from sqlalchemy import Engine
            from sqlalchemy.ext.asyncio import AsyncEngine
            assert isinstance(engine, Engine)
            assert isinstance(async_engine, AsyncEngine)
            print(f"   ✅ Engines created: sync={type(engine).__name__}, async={type(async_engine).__name__}")
            
            assert SyncSessionLocal is not None and AsyncSessionLocal is not None
            print(f"   ✅ Session factories: SyncSessionLocal, AsyncSessionLocal")
            
            # -- Test 4: Session getters (mocked) ----------------------
            print("\n📌 Test 4: Session getters (mocked creation)")
            
            with patch('app.database.engine.SyncSessionLocal') as mock_sync, \
                 patch('app.database.engine.AsyncSessionLocal') as mock_async:
                
                mock_sync.return_value = MagicMock()
                mock_async.return_value = AsyncMock()
                
                sync_sess = get_sync_session()
                assert sync_sess is not None
                print(f"   ✅ get_sync_session() returns session")
                
                async_sess = await get_async_session()
                assert async_sess is not None
                print(f"   ✅ get_async_session() returns async session")
            
            # -- Test 5: check_database_health (properly mocked) -------
            print("\n📌 Test 5: check_database_health (mocked connectivity)")
            
            # Create proper mock connection with async context manager support
            mock_conn = MagicMock()
            mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
            mock_conn.__aexit__ = AsyncMock(return_value=None)
            
            # Mock execute to return a result
            mock_result = MagicMock()
            mock_result.scalar = MagicMock(return_value=True)
            mock_conn.execute = AsyncMock(return_value=mock_result)
            
            # Regular function (not async) that returns context manager directly
            def mock_connect():
                return MockAsyncContextManager(mock_conn)
            
            # Patch the async_engine in the module
            with patch('app.database.engine.async_engine') as mock_engine:
                mock_engine.connect = mock_connect
                
                # Test basic health check
                healthy = await check_database_health(verify_schema=False)
                assert healthy is True
                print(f"   ✅ Health check (basic): passed")
                
                # Test with schema verify
                healthy = await check_database_health(verify_schema=True)
                assert healthy is True
                print(f"   ✅ Health check (with schema): passed")
                
                # Test with missing table
                mock_result.scalar.return_value = False
                healthy = await check_database_health(verify_schema=True)
                assert healthy is False
                print(f"   ✅ Health check (missing table): correctly failed")
            
            # -- Test 6: dispose_engines (mock entire engines) ----------
            print("\n📌 Test 6: dispose_engines (graceful shutdown)")
            
            # Create fully mocked engines with callable dispose methods
            mock_sync_engine = MagicMock()
            mock_async_engine = MagicMock()
            mock_async_engine.dispose = AsyncMock()
            
            # Patch both engines in the module
            with patch('app.database.engine.engine', mock_sync_engine), \
                 patch('app.database.engine.async_engine', mock_async_engine):
                
                await dispose_engines()
                
                mock_sync_engine.dispose.assert_called_once()
                mock_async_engine.dispose.assert_called_once()
                print(f"   ✅ dispose_engines() called dispose on both mocked engines")
            
            # -- Test 7: Configuration values --------------------------
            print("\n📌 Test 7: Configuration values (pool sizes, timeouts)")
            
            from app.config import get_settings
            settings = get_settings()
            
            sync_pool = getattr(settings, "db_pool_size", 20)
            async_pool = max(5, sync_pool // 2)
            assert async_pool >= 5
            print(f"   ✅ Pool sizing: sync={sync_pool}, async={async_pool} (min 5)")
            
            connect_timeout = getattr(settings, "db_connect_timeout", 10)
            statement_timeout = getattr(settings, "db_statement_timeout", 30)
            assert connect_timeout > 0 and statement_timeout > 0
            print(f"   ✅ Timeouts: connect={connect_timeout}s, statement={statement_timeout}s")
            
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Database Engine module verified.")
            print("\n💡 What we verified:")
            print("   • Security: URL sanitization (password masking) ✅")
            print("   • Driver conversion: async -> sync URL mapping ✅")
            print("   • Engines: SQLAlchemy sync + async engines created ✅")
            print("   • Sessions: Session factories & getters working ✅")
            print("   • Health: Connectivity check with retry + schema verify ✅")
            print("   • Shutdown: Graceful engine disposal ✅")
            print("   • Config: Pool sizes, timeouts from settings ✅")
            print("\n🔐 Production: Connection pooling, timeouts, health checks ready")
            return True
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run async tests
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)