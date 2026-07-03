"""
One-time migration: update workspaces that have old plan defaults.

Run ONCE after deploying the billing plan limits update:
    python migrate_plan_defaults.py

What it does:
  1. Workspaces on old "starter" default (max_docs=100) that never subscribed
     → downgrade to "free" plan limits (5 docs, 50 queries, 0.1 GB)
  2. Workspaces on old "business" plan → rename to "pro"
  3. Adds query_count_reset_at column if missing (already handled by ensure_usage_schema
     but safe to run again)

Safe to run multiple times (idempotent).
"""
import asyncio
from sqlalchemy import text
from app.database.engine import async_engine


async def run():
    async with async_engine.begin() as conn:

        # 1. Old default "starter" workspaces that never paid → move to "free"
        result = await conn.execute(text("""
            UPDATE workspaces
            SET plan            = 'free',
                max_docs        = 5,
                max_queries_per_day = 50,
                max_storage_gb  = 0.1
            WHERE plan = 'starter'
              AND (stripe_subscription_id IS NULL OR stripe_subscription_id = '')
              AND (subscription_status = 'none' OR subscription_status IS NULL)
        """))
        print(f"  Downgraded {result.rowcount} unpaid 'starter' workspaces → 'free'")

        # 2. Old "business" plan → rename to "pro"
        result = await conn.execute(text("""
            UPDATE workspaces
            SET plan            = 'pro',
                max_docs        = 1000,
                max_storage_gb  = 20.0
            WHERE plan = 'business'
        """))
        print(f"  Renamed {result.rowcount} 'business' workspaces → 'pro'")

        # 3. Paid "starter" workspaces → apply correct new limits
        result = await conn.execute(text("""
            UPDATE workspaces
            SET max_docs            = 100,
                max_queries_per_day = 500,
                max_storage_gb      = 2.0
            WHERE plan = 'starter'
              AND stripe_subscription_id IS NOT NULL
              AND subscription_status = 'active'
        """))
        print(f"  Updated {result.rowcount} active 'starter' subscriptions with correct limits")

        # 4. Ensure query_count_reset_at column exists (idempotent)
        await conn.execute(text("""
            ALTER TABLE workspaces
            ADD COLUMN IF NOT EXISTS query_count_reset_at DATE NOT NULL DEFAULT CURRENT_DATE
        """))
        print("  query_count_reset_at column: OK")

    print("\nMigration complete.")


if __name__ == "__main__":
    import os, sys
    sys.path.insert(0, os.path.dirname(__file__))
    asyncio.run(run())
