# backend/app/api/routes/razorpay.py
"""
Razorpay billing routes for Indian clients — INR, UPI, NetBanking.

Endpoints:
  POST /razorpay/order       — create a Razorpay order (one-time) or subscription
  POST /razorpay/verify      — verify payment signature after client-side checkout
  POST /razorpay/webhook     — Razorpay webhook receiver (signature-verified)

Setup:
  1. Create account at https://dashboard.razorpay.com
  2. Create Plans for starter/pro at Dashboard → Subscriptions → Plans
  3. Set env vars: RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET
  4. Set RAZORPAY_PLAN_ID_STARTER, RAZORPAY_PLAN_ID_PRO
"""
from __future__ import annotations

import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin
from app.config import get_settings
from app.core.billing_manager import (
    get_billing_state,
    get_workspace_id_by_stripe_customer,
    set_stripe_customer,
    update_subscription,
)
from app.core.plan_registry import CANCELLED_DOWNGRADE_PLAN, PLAN_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/razorpay", tags=["razorpay"])

RAZORPAY_API = "https://api.razorpay.com/v1"


def _razorpay_configured() -> tuple[str, str]:
    """Return (key_id, key_secret) or raise 503."""
    s = get_settings()
    if not s.razorpay_key_id or not s.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Razorpay is not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set)")
    return s.razorpay_key_id, s.razorpay_key_secret


class RazorpayOrderRequest(BaseModel):
    plan: str = Field(..., min_length=1, max_length=50)


class RazorpayVerifyRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    plan: str


@router.get("/config")
async def razorpay_config() -> dict:
    """Return public Razorpay key so the frontend can init Razorpay.js."""
    s = get_settings()
    if not s.razorpay_key_id:
        raise HTTPException(status_code=503, detail="Razorpay not configured")
    return {"key_id": s.razorpay_key_id}


@router.post("/subscribe")
async def create_subscription(
    body: RazorpayOrderRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    """Create a Razorpay Subscription for the given plan."""
    key_id, key_secret = _razorpay_configured()
    s = get_settings()

    entry = PLAN_REGISTRY.get(body.plan)
    if entry is None or not entry["self_serve"]:
        raise HTTPException(status_code=422, detail=f"Plan '{body.plan}' is not available for self-serve checkout")

    plan_id_map = {
        "starter": s.razorpay_plan_id_starter,
        "pro":     s.razorpay_plan_id_pro,
    }
    plan_id = plan_id_map.get(body.plan)
    if not plan_id:
        raise HTTPException(status_code=503, detail=f"No Razorpay plan configured for '{body.plan}'")

    payload = {
        "plan_id": plan_id,
        "total_count": 12,   # 12 billing cycles (monthly → 1 year auto-renews)
        "quantity": 1,
        "notes": {
            "workspace_id": user.workspace_id,
            "plan": body.plan,
            "email": user.email,
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{RAZORPAY_API}/subscriptions",
            auth=(key_id, key_secret),
            json=payload,
        )

    if resp.status_code >= 400:
        logger.error(f"Razorpay subscription creation failed: {resp.text}")
        raise HTTPException(status_code=502, detail=f"Razorpay error: {resp.json().get('error', {}).get('description', resp.text)}")

    data = resp.json()
    return {
        "subscription_id": data["id"],
        "short_url": data.get("short_url"),
        "key_id": key_id,
        "plan": body.plan,
        "amount_inr": entry["price_inr"],
    }


@router.post("/verify")
async def verify_payment(
    body: RazorpayVerifyRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    """
    Verify Razorpay payment signature after client-side checkout completes.
    On success, activates the plan immediately — don't wait for webhook.
    """
    _, key_secret = _razorpay_configured()

    # Razorpay signature: HMAC-SHA256(payment_id + "|" + subscription_id, key_secret)
    expected = hmac.new(
        key_secret.encode(),
        f"{body.razorpay_payment_id}|{body.razorpay_subscription_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    await update_subscription(
        user.workspace_id,
        plan=body.plan,
        subscription_id=body.razorpay_subscription_id,
        status="active",
    )
    logger.info(f"Razorpay payment verified for workspace {user.workspace_id}, plan={body.plan}")
    return {"success": True, "plan": body.plan}


@router.post("/webhook", include_in_schema=False)
async def razorpay_webhook(request: Request) -> dict:
    """Razorpay webhook — verified by X-Razorpay-Signature header."""
    s = get_settings()
    if not s.razorpay_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(s.razorpay_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        logger.warning("Razorpay webhook signature mismatch")
        raise HTTPException(status_code=400, detail="Invalid signature")

    import json
    event = json.loads(payload)
    event_type = event.get("event", "")

    if event_type == "subscription.activated":
        sub = event.get("payload", {}).get("subscription", {}).get("entity", {})
        notes = sub.get("notes", {})
        workspace_id = notes.get("workspace_id")
        plan = notes.get("plan", "starter")
        if workspace_id:
            await update_subscription(workspace_id, plan=plan, subscription_id=sub.get("id"), status="active")

    elif event_type in ("subscription.cancelled", "subscription.completed"):
        sub = event.get("payload", {}).get("subscription", {}).get("entity", {})
        notes = sub.get("notes", {})
        workspace_id = notes.get("workspace_id")
        if workspace_id:
            await update_subscription(workspace_id, plan=CANCELLED_DOWNGRADE_PLAN, subscription_id=None, status="canceled")

    elif event_type == "payment.failed":
        sub = event.get("payload", {}).get("payment", {}).get("entity", {})
        notes = sub.get("notes", {})
        workspace_id = notes.get("workspace_id")
        if workspace_id:
            state = await get_billing_state(workspace_id)
            await update_subscription(
                workspace_id,
                plan=state.plan if state else "free",
                subscription_id=state.stripe_subscription_id if state else None,
                status="past_due",
            )

    else:
        logger.debug(f"Unhandled Razorpay event: {event_type}")

    return {"received": True}


__all__ = ["router"]
