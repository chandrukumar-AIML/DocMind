# backend/app/core/plan_registry.py
"""
Subscription plan definitions — single source of truth for plan limits and Stripe mapping.

Plan keys: free | starter | pro | enterprise
These keys match the `plan` column on the Workspace model and are used by:
  - billing_manager.update_subscription()  (syncs limits onto workspace row)
  - usage_tracker.check_*()               (reads limits from workspace row)
  - billing.py routes                      (checkout, webhook, list_plans)
  - plan_llm.get_llm_for_plan()           (maps plan → LLM tier)
"""

from __future__ import annotations

# Monthly query limits — None means unlimited.
# -1 is NOT used; None/missing means skip the check entirely.
PLAN_REGISTRY: dict[str, dict] = {
    "free": {
        "label": "Free",
        "price_usd": 0,
        "price_inr": 0,
        "price_display": "Free",
        "self_serve": False,   # default on signup — no checkout needed
        "max_docs": 5,
        "max_queries_per_month": 50,
        "max_storage_gb": 0.1,
        "llm_tier": "basic",   # used by plan_llm.get_llm_for_plan()
    },
    "starter": {
        "label": "Starter",
        "price_usd": 29,
        "price_inr": 2499,
        "price_display": "$29/mo",
        "self_serve": True,
        "max_docs": 100,
        "max_queries_per_month": 500,
        "max_storage_gb": 2.0,
        "llm_tier": "standard",
    },
    "pro": {
        "label": "Pro",
        "price_usd": 79,
        "price_inr": 6599,
        "price_display": "$79/mo",
        "self_serve": True,
        "max_docs": 1000,
        "max_queries_per_month": None,   # unlimited
        "max_storage_gb": 20.0,
        "llm_tier": "advanced",
    },
    "enterprise": {
        "label": "Enterprise",
        "price_usd": 299,
        "price_inr": 24999,
        "price_display": "From $299/mo",
        "self_serve": False,   # negotiated; provisioned via superadmin
        "max_docs": None,      # unlimited
        "max_queries_per_month": None,
        "max_storage_gb": None,
        "llm_tier": "advanced",
    },
}

# Convenience: plans that downgrade to when a Stripe subscription is cancelled
CANCELLED_DOWNGRADE_PLAN = "free"


def get_plan(plan_id: str) -> dict:
    """Return plan dict, falling back to 'free' for unknown IDs."""
    return PLAN_REGISTRY.get(plan_id, PLAN_REGISTRY["free"])


__all__ = ["PLAN_REGISTRY", "CANCELLED_DOWNGRADE_PLAN", "get_plan"]
