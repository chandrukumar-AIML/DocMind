# backend/app/core/plan_registry.py
"""
Subscription plan definitions for Stripe billing.

Data-driven, same pattern as app.core.llm_providers.PROVIDER_REGISTRY — a single dict to
edit when pricing changes, rather than scattering plan logic across routes.

Plan keys match the existing "starter" / "business" / "enterprise" enum already used by
the superadmin manual-workspace-creation endpoint (app/api/routes/superadmin.py's
WorkspaceCreateRequest.plan pattern) — reusing it here instead of inventing a competing
"pro" name keeps one consistent plan vocabulary across the manual (superadmin) and
self-serve (Stripe) paths.
"""

from __future__ import annotations

PLAN_REGISTRY: dict[str, dict] = {
    "starter": {
        "label": "Starter",
        "price_display": "Free",
        "self_serve": False,  # default plan on signup — no Stripe Checkout needed to get on it
        "max_docs": 100,
        "max_queries_per_day": 500,
        "max_storage_gb": 5.0,
    },
    "business": {
        "label": "Business",
        "price_display": "$49/mo",
        "self_serve": True,  # available via Stripe Checkout
        "max_docs": 1000,
        "max_queries_per_day": 5000,
        "max_storage_gb": 50.0,
    },
    "enterprise": {
        "label": "Enterprise",
        "price_display": "Contact us",
        "self_serve": False,  # negotiated — no self-serve checkout
        "max_docs": None,
        "max_queries_per_day": None,
        "max_storage_gb": None,
    },
}


__all__ = ["PLAN_REGISTRY"]
