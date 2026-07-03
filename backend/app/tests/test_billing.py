"""Tests for Stripe billing: plan registry, schema, route gating, webhook handling.

No real Stripe API calls are made — stripe.checkout.Session.create, stripe.Customer.create,
stripe.billing_portal.Session.create, and stripe.Webhook.construct_event are all mocked.
"""

import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.billing_manager import (
    ensure_billing_schema,
    get_billing_state,
    get_workspace_id_by_stripe_customer,
    set_stripe_customer,
    update_subscription,
)
from app.core.plan_registry import PLAN_REGISTRY
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """See app/tests/test_llm_settings.py for why this is needed (event-loop-per-test
    vs a module-level asyncpg connection pool)."""
    yield
    from app.database.engine import async_engine

    await async_engine.dispose()


def test_plan_registry_shape():
    assert set(PLAN_REGISTRY) == {"starter", "business", "enterprise"}
    for entry in PLAN_REGISTRY.values():
        assert "label" in entry
        assert "price_display" in entry
        assert "self_serve" in entry
    assert PLAN_REGISTRY["starter"]["self_serve"] is False
    assert PLAN_REGISTRY["business"]["self_serve"] is True
    assert PLAN_REGISTRY["enterprise"]["self_serve"] is False


@pytest.mark.asyncio
async def test_schema_creation_idempotent():
    await ensure_billing_schema()
    await ensure_billing_schema()


@pytest.mark.asyncio
async def test_billing_state_round_trip():
    import uuid

    from app.database.engine import async_engine
    from sqlalchemy import text as sql_text

    async with async_engine.connect() as conn:
        row = (await conn.execute(sql_text("SELECT id FROM workspaces ORDER BY id LIMIT 1"))).first()
    if row is None:
        pytest.skip("No workspace rows available to test against")
    ws_id = str(row[0])

    # Unique per run — stripe_customer_id has no UNIQUE constraint, so a fixed literal
    # would collide with leftover state from a previous run that didn't restore cleanly.
    fake_customer_id = f"cus_test_{uuid.uuid4().hex[:16]}"

    await ensure_billing_schema()
    original = await get_billing_state(ws_id)
    try:
        await set_stripe_customer(ws_id, fake_customer_id)
        state = await get_billing_state(ws_id)
        assert state.stripe_customer_id == fake_customer_id

        found_ws = await get_workspace_id_by_stripe_customer(fake_customer_id)
        assert found_ws == ws_id

        await update_subscription(ws_id, plan="business", subscription_id="sub_test_roundtrip", status="active")
        state2 = await get_billing_state(ws_id)
        assert state2.plan == "business"
        assert state2.subscription_status == "active"
    finally:
        # restore whatever was there before this test ran, including stripe_customer_id
        # (update_subscription doesn't touch that column, so it needs a direct restore)
        async with async_engine.begin() as conn:
            await conn.execute(
                sql_text("UPDATE workspaces SET stripe_customer_id = :cid WHERE id = :ws_id"),
                {"cid": original.stripe_customer_id if original else None, "ws_id": ws_id},
            )
        if original:
            await update_subscription(
                ws_id,
                plan=original.plan,
                subscription_id=original.stripe_subscription_id,
                status=original.subscription_status,
            )


def test_billing_plans_route_is_public():
    response = client.get("/api/v1/billing/plans")
    assert response.status_code == 200
    body = response.json()
    assert any(p["id"] == "business" for p in body["plans"])


def test_billing_routes_require_auth():
    assert client.get("/api/v1/billing/subscription").status_code in (401, 403)
    assert client.post("/api/v1/billing/checkout", json={"plan": "business"}).status_code in (401, 403)
    assert client.post("/api/v1/billing/portal").status_code in (401, 403)


def test_webhook_rejects_bad_signature():
    response = client.post(
        "/api/v1/billing/webhook",
        content=b'{"type": "checkout.session.completed"}',
        headers={"stripe-signature": "t=123,v1=not-a-real-signature"},
    )
    # 400 (bad signature) or 503 (no webhook secret configured in this test env) are both
    # acceptable — either way it must NOT process the event as if it were genuine.
    assert response.status_code in (400, 503)


def test_webhook_accepts_valid_signature_and_updates_subscription():
    """Construct a real Stripe-style signed payload and verify our handler processes it,
    using a fake webhook secret injected via settings (no real Stripe account needed)."""
    from app.config import get_settings

    fake_secret = "whsec_test_fake_secret_for_unit_test"
    payload = json.dumps(
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "metadata": {"workspace_id": "00000000-0000-0000-0000-000000000000", "plan": "business"},
                    "subscription": "sub_fake123",
                }
            },
        }
    ).encode()

    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}".encode()
    signature = hmac.new(fake_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={signature}"

    with patch.object(get_settings(), "stripe_webhook_secret", fake_secret), patch(
        "app.api.routes.billing.update_subscription"
    ) as mock_update:
        mock_update.return_value = None
        response = client.post(
            "/api/v1/billing/webhook",
            content=payload,
            headers={"stripe-signature": header},
        )

    assert response.status_code == 200
    assert response.json() == {"received": True}
    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args
    assert call_kwargs.args[0] == "00000000-0000-0000-0000-000000000000" or call_kwargs.kwargs.get(
        "workspace_id"
    ) == "00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_webhook_syncs_real_plan_and_limits_end_to_end():
    """The revenue-critical path, unmocked: a signed checkout.session.completed webhook
    must upgrade a real workspace to `business` AND raise its usage-enforcement limits;
    a subscription.deleted webhook must downgrade it and lower the limits again.

    Only update_subscription's DB writes are exercised (no real Stripe) — the webhook
    secret is injected via settings, and the whole thing runs against the live app +
    real Postgres, then restores the workspace exactly."""
    from app.config import get_settings
    from app.database.engine import async_engine
    from sqlalchemy import text as sql_text

    cols = ("plan, subscription_status, stripe_subscription_id, stripe_customer_id, "
            "max_docs, max_queries_per_day, max_storage_gb")

    async with async_engine.connect() as conn:
        row = (await conn.execute(sql_text("SELECT id FROM workspaces ORDER BY id LIMIT 1"))).first()
    if row is None:
        pytest.skip("No workspace rows available to test against")
    ws_id = str(row[0])

    async def _snap():
        async with async_engine.connect() as conn:
            r = (await conn.execute(
                sql_text(f"SELECT {cols} FROM workspaces WHERE id = :w"), {"w": ws_id}
            )).mappings().first()
        return dict(r)

    def _signed(event: dict, secret: str):
        payload = json.dumps(event).encode()
        ts = int(time.time())
        sig = hmac.new(secret.encode(), f"{ts}.{payload.decode()}".encode(), hashlib.sha256).hexdigest()
        return payload, {"stripe-signature": f"t={ts},v1={sig}"}

    fake_secret = "whsec_test_e2e_limits_sync"
    original = await _snap()
    try:
        # Give the workspace a customer id so the subscription.deleted lookup resolves.
        async with async_engine.begin() as conn:
            await conn.execute(
                sql_text("UPDATE workspaces SET stripe_customer_id = :c WHERE id = :w"),
                {"c": "cus_test_e2e_limits", "w": ws_id},
            )

        with patch.object(get_settings(), "stripe_webhook_secret", fake_secret):
            # Upgrade → business
            up_payload, up_headers = _signed(
                {"type": "checkout.session.completed",
                 "data": {"object": {"metadata": {"workspace_id": ws_id, "plan": "business"},
                                     "subscription": "sub_test_e2e"}}},
                fake_secret,
            )
            await async_engine.dispose()
            resp_up = client.post("/api/v1/billing/webhook", content=up_payload, headers=up_headers)
            await async_engine.dispose()
            assert resp_up.status_code == 200, resp_up.text

            after_up = await _snap()
            assert after_up["plan"] == "business"
            assert after_up["subscription_status"] == "active"
            assert after_up["max_docs"] == PLAN_REGISTRY["business"]["max_docs"]
            assert after_up["max_queries_per_day"] == PLAN_REGISTRY["business"]["max_queries_per_day"]
            assert after_up["max_storage_gb"] == PLAN_REGISTRY["business"]["max_storage_gb"]

            # Cancel → starter
            down_payload, down_headers = _signed(
                {"type": "customer.subscription.deleted",
                 "data": {"object": {"id": "sub_test_e2e", "customer": "cus_test_e2e_limits"}}},
                fake_secret,
            )
            await async_engine.dispose()
            resp_down = client.post("/api/v1/billing/webhook", content=down_payload, headers=down_headers)
            await async_engine.dispose()
            assert resp_down.status_code == 200, resp_down.text

            after_down = await _snap()
            assert after_down["plan"] == "starter"
            assert after_down["subscription_status"] == "canceled"
            assert after_down["max_docs"] == PLAN_REGISTRY["starter"]["max_docs"]
    finally:
        async with async_engine.begin() as conn:
            await conn.execute(
                sql_text(f"""UPDATE workspaces SET plan=:plan, subscription_status=:st,
                             stripe_subscription_id=:sid, stripe_customer_id=:cid,
                             max_docs=:md, max_queries_per_day=:mq, max_storage_gb=:ms WHERE id=:w"""),
                {"plan": original["plan"], "st": original["subscription_status"],
                 "sid": original["stripe_subscription_id"], "cid": original["stripe_customer_id"],
                 "md": original["max_docs"], "mq": original["max_queries_per_day"],
                 "ms": original["max_storage_gb"], "w": ws_id},
            )
        await async_engine.dispose()
