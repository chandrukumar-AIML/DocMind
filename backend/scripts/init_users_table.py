# backend/scripts/init_users_table.py
#!/usr/bin/env python3
"""Initialize users table for auth."""
import asyncio, sys, os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + "/.."))

from sqlalchemy import create_engine, text, inspect
from app.config import get_settings

def init_users_table():
    settings = get_settings()
    # Convert asyncpg URL to psycopg2 for sync execution
    sync_url = settings.database_url.replace("+asyncpg", "+psycopg2")
    
    try:
        engine = create_engine(sync_url)
        inspector = inspect(engine)
        
        if "users" in inspector.get_table_names():
            print("✅ Users table already exists")
            return
        
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE users (
                    id VARCHAR(36) PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    hashed_password VARCHAR(255) NOT NULL,
                    workspace_id VARCHAR(36) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'user',
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                );
                CREATE INDEX idx_users_email ON users(email);
                CREATE INDEX idx_users_workspace ON users(workspace_id);
            """))
            conn.commit()
            print("✅ Users table created successfully")
            
    except Exception as e:
        print(f"❌ Failed to create users table: {e}")
        sys.exit(1)

if __name__ == "__main__":
    init_users_table()