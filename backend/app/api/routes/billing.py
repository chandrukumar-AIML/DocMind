# backend/app/api/routes/billing.py
"""Stripe billing — plan listing, checkout, customer portal, and webhook sync."""

from __future__ import annotations

import logging
from typing import Optional

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from sqlalchemy import text

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin
from app.config import get_settings
from app.core.billing_manager import (
    get_billing_state,
    get_workspace_id_by_stripe_customer,
    set_stripe_customer,
    update_subscription,
)
from app.core.plan_registry import PLAN_REGISTRY
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    plan: str = Field(..., min_length=1, max_length=50)


def _stripe_configured() -> None:
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Billing is not configured (STRIPE_SECRET_KEY not set)")
    stripe.api_key = settings.stripe_secret_key


@router.get("/plans")
async def list_plans() -> dict:
    return {
        "plans": [
            {"id": key, **{k: v for k, v in entry.items()}}
            for key, entry in PLAN_REGISTRY.items()
        ]
    }


@router.get("/subscription")
async def get_subscription(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    state = await get_billing_state(user.workspace_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return {
        "plan": state.plan,
        "subscription_status": state.subscription_status,
        "has_stripe_customer": state.stripe_customer_id is not None,
    }


@router.get("/usage")
async def get_usage(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    """Current usage against the workspace's plan limits — the visible proof that
    usage-limit enforcement (app/middleware/usage_limiter.py) is real, not cosmetic."""
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT doc_count, max_docs, query_count_today, max_queries_per_day,
                       storage_used_mb, max_storage_gb
                FROM workspaces WHERE id = :workspace_id
            """),
                {"workspace_id": user.workspace_id},
            )
        ).mappings().first()

    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")

    return {
        "docs": {"used": row["doc_count"], "limit": row["max_docs"]},
        "queries_today": {"used": row["query_count_today"], "limit": row["max_queries_per_day"]},
        "storage_mb": {"used": round(row["storage_used_mb"], 2), "limit_mb": round(row["max_storage_gb"] * 1024, 2)},
    }


@router.post("/checkout")
async def start_checkout(
    body: CheckoutRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    _stripe_configured()
    settings = get_settings()

    entry = PLAN_REGISTRY.get(body.plan)
    if entry is None:
        raise HTTPException(status_code=422, detail=f"Unknown plan '{body.plan}'. Supported: {list(PLAN_REGISTRY)}")
    if not entry["self_serve"]:
        raise HTTPException(
            status_code=422,
            detail=f"'{body.plan}' is not self-serve — contact sales instead of checkout.",
        )

    price_map = {
        "starter": settings.stripe_price_id_starter,
        "pro":     settings.stripe_price_id_pro or settings.stripe_price_id_business,  # legacy fallback
    }
    price_id = price_map.get(body.plan)
    if not price_id:
        raise HTTPException(status_code=503, detail=f"No Stripe price configured for plan '{body.plan}'")

    state = await get_billing_state(user.workspace_id)
    customer_id = state.stripe_customer_id if state else None

    try:
        if customer_id is None:
            customer = stripe.Customer.create(
                email=user.email,
                metadata={"workspace_id": user.workspace_id},
            )
            customer_id = customer.id
            await set_stripe_customer(user.workspace_id, customer_id)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=f"{settings.frontend_url}/?billing=success",
            cancel_url=f"{settings.frontend_url}/?billing=cancelled",
            metadata={"workspace_id": user.workspace_id, "plan": body.plan},
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe checkout creation failed for workspace {user.workspace_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message or str(e)}")

    return {"checkout_url": session.url}


@router.post("/portal")
async def open_billing_portal(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    _stripe_configured()
    settings = get_settings()

    state = await get_billing_state(user.workspace_id)
    if state is None or state.stripe_customer_id is None:
        raise HTTPException(status_code=404, detail="No billing account yet — subscribe to a plan first")

    try:
        session = stripe.billing_portal.Session.create(
            customer=state.stripe_customer_id,
            return_url=f"{settings.frontend_url}/",
        )
    except stripe.error.StripeError as e:
        logger.error(f"Stripe portal session failed for workspace {user.workspace_id}: {e}")
        raise HTTPException(status_code=502, detail=f"Stripe error: {e.user_message or str(e)}")

    return {"portal_url": session.url}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request) -> dict:
    """Stripe event receiver — verified by signature, not auth (Stripe calls this directly)."""
    settings = get_settings()
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.stripe_webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning(f"Stripe webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        workspace_id = (data.get("metadata") or {}).get("workspace_id")
        plan = (data.get("metadata") or {}).get("plan", "business")
        subscription_id = data.get("subscription")
        if workspace_id:
            await update_subscription(workspace_id, plan=plan, subscription_id=subscription_id, status="active")

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        workspace_id = await get_workspace_id_by_stripe_customer(customer_id) if customer_id else None
        if workspace_id:
            status = data.get("status", "active")
            current_state = await get_billing_state(workspace_id)
            plan = current_state.plan if current_state else "business"
            await update_subscription(workspace_id, plan=plan, subscription_id=data.get("id"), status=status)

    elif event_type == "customer.subscription.deleted":
        from app.core.plan_registry import CANCELLED_DOWNGRADE_PLAN
        customer_id = data.get("customer")
        workspace_id = await get_workspace_id_by_stripe_customer(customer_id) if customer_id else None
        if workspace_id:
            await update_subscription(workspace_id, plan=CANCELLED_DOWNGRADE_PLAN, subscription_id=None, status="canceled")

    elif event_type == "invoice.payment_failed":
        # Mark subscription as past_due so the frontend can show a payment banner
        customer_id = data.get("customer")
        workspace_id = await get_workspace_id_by_stripe_customer(customer_id) if customer_id else None
        if workspace_id:
            current_state = await get_billing_state(workspace_id)
            plan = current_state.plan if current_state else "free"
            sub_id = current_state.stripe_subscription_id if current_state else None
            await update_subscription(workspace_id, plan=plan, subscription_id=sub_id, status="past_due")
            logger.warning(f"Payment failed for workspace {workspace_id} — marked past_due")

    else:
        logger.debug(f"Unhandled Stripe event type: {event_type}")

    return {"received": True}


__all__ = ["router"]
