# backend/init_db.py
"""Initialize database tables for DocuMind AI (FINAL)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def init_db():
    print("🗄️  Initializing DocuMind AI database...")
    
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        from app.config import get_settings
        
        # 🔥 CRITICAL: Import ALL models BEFORE create_all() so they register with Base
        print("📦 Importing models...")
        from app.auth.models import User, Workspace, WorkspaceMember, UserRoleEnum
        from app.provenance.models import Answer, Citation, DocumentStore
        from app.database.base import Base
        
        settings = get_settings()
        db_url = getattr(settings, "database_url", os.getenv("DATABASE_URL"))
        if not db_url:
            raise ValueError("DATABASE_URL not configured")
        
        print(f"🔗 Connecting to: {db_url[:50]}...")
        engine = create_async_engine(db_url, echo=False)
        
        # 🧹 Clean slate: drop existing tables (dev mode only!)
        print("🧹 Cleaning existing schema...")
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS workspace_members CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS workspaces CASCADE"))
            await conn.execute(text("DROP TYPE IF EXISTS user_role_enum CASCADE"))
        
        # 🔨 Create ALL tables with correct schema
        print("🔨 Creating tables with correct UUID types...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        await engine.dispose()
        print("✅ All tables created successfully!")
        
        # 🔍 Verify primary key types match
        engine = create_async_engine(db_url, echo=False)
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT table_name, column_name, data_type 
                FROM information_schema.columns 
                WHERE table_schema = 'public' 
                AND table_name IN ('users', 'workspaces', 'workspace_members')
                AND column_name = 'id'
                ORDER BY table_name;
            """))
            print("\n📋 Primary key types:")
            all_uuid = True
            for row in result:
                print(f"  {row[0]}.id = {row[2]}")
                if row[2] != 'uuid':
                    all_uuid = False
            if all_uuid:
                print("✅ All IDs are UUID - foreign keys will work!")
            else:
                print("⚠️  Warning: Some IDs are not UUID")
        await engine.dispose()
        
        print("\n🎯 Next steps:")
        print("  1. Run: python create_test_user.py")
        print("  2. Restart: uvicorn app.main:app --reload")
        print("  3. Test login in Swagger UI")
        
    except Exception as e:
        print(f"❌ Failed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(init_db())
    