"""
Stripe billing state — schema repair for the 3 billing columns on `workspaces`, plus
plain CRUD helpers used by app/api/routes/billing.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

from app.core.plan_registry import PLAN_REGISTRY
from app.database.engine import async_engine

logger = logging.getLogger(__name__)


@dataclass
class BillingState:
    workspace_id: str
    plan: str
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    subscription_status: str


async def ensure_billing_schema() -> None:
    """
    Add Stripe billing columns to the existing `workspaces` table.

    `plan`/`max_docs`/etc. already exist on Workspace (app/auth/models.py) from an earlier
    migration — these 3 columns follow the same "add to an already-live table" situation,
    so `Base.metadata.create_all()` (which only creates missing tables, never alters
    existing ones) can't add them. Repaired the same way as the rest of this codebase's
    ensure_*_schema() helpers: idempotent raw ALTER TABLE.
    """
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)")
        )
        await conn.execute(
            text("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255)")
        )
        await conn.execute(
            text(
                "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
                "subscription_status VARCHAR(30) NOT NULL DEFAULT 'none'"
            )
        )
    logger.info("Billing schema verified")


async def get_billing_state(workspace_id: str) -> Optional[BillingState]:
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT plan, stripe_customer_id, stripe_subscription_id, subscription_status
                FROM workspaces WHERE id = :workspace_id
            """),
                {"workspace_id": workspace_id},
            )
        ).mappings().first()

    if row is None:
        return None

    return BillingState(
        workspace_id=workspace_id,
        plan=row["plan"],
        stripe_customer_id=row["stripe_customer_id"],
        stripe_subscription_id=row["stripe_subscription_id"],
        subscription_status=row["subscription_status"],
    )


async def set_stripe_customer(workspace_id: str, customer_id: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("UPDATE workspaces SET stripe_customer_id = :customer_id WHERE id = :workspace_id"),
            {"customer_id": customer_id, "workspace_id": workspace_id},
        )


async def get_workspace_id_by_stripe_customer(customer_id: str) -> Optional[str]:
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id FROM workspaces WHERE stripe_customer_id = :customer_id"),
                {"customer_id": customer_id},
            )
        ).first()
    return str(row[0]) if row else None


async def update_subscription(
    workspace_id: str,
    plan: str,
    subscription_id: Optional[str],
    status: str,
) -> None:
    """
    Update a workspace's subscription state AND sync its usage-enforcement limits
    (max_docs/max_queries_per_day/max_storage_gb) from PLAN_REGISTRY — without this, a
    workspace that upgrades via Stripe keeps its old plan's limits forever, since
    app/middleware/usage_limiter.py enforces against these columns, not against `plan`
    directly.
    """
    plan_limits = PLAN_REGISTRY.get(plan, {})
    max_docs    = plan_limits.get("max_docs")
    # Registry uses max_queries_per_month; the DB column is max_queries_per_day (monthly budget)
    max_queries = plan_limits.get("max_queries_per_month")
    max_storage = plan_limits.get("max_storage_gb")

    async with async_engine.begin() as conn:
        if plan_limits and max_docs is not None:
            await conn.execute(
                text("""
                    UPDATE workspaces
                    SET plan = :plan, stripe_subscription_id = :subscription_id, subscription_status = :status,
                        max_docs = :max_docs, max_queries_per_day = :max_queries, max_storage_gb = :max_storage
                    WHERE id = :workspace_id
                """),
                {
                    "plan": plan,
                    "subscription_id": subscription_id,
                    "status": status,
                    "workspace_id": workspace_id,
                    "max_docs": max_docs,
                    "max_queries": max_queries,
                    "max_storage": max_storage,
                },
            )
        else:
            # Unknown plan string, or enterprise (all-None limits) —
            # leave existing limit columns untouched; enterprise limits are set via superadmin.
            await conn.execute(
                text("""
                    UPDATE workspaces
                    SET plan = :plan, stripe_subscription_id = :subscription_id, subscription_status = :status
                    WHERE id = :workspace_id
                """),
                {
                    "plan": plan,
                    "subscription_id": subscription_id,
                    "status": status,
                    "workspace_id": workspace_id,
                },
            )
    logger.info(f"Workspace {workspace_id} subscription updated: plan={plan}, status={status}")


__all__ = [
    "BillingState",
    "ensure_billing_schema",
    "get_billing_state",
    "set_stripe_customer",
    "get_workspace_id_by_stripe_customer",
    "update_subscription",
]
