"""Tests for usage-limit enforcement: threshold checks, lazy daily reset, plan-limit
sync on subscription change, and the UsageLimiterMiddleware itself.
"""

import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.billing_manager import update_subscription
from app.core.plan_registry import PLAN_REGISTRY
from app.core.usage_tracker import (
    check_doc_limit,
    check_query_limit,
    check_storage_limit,
    ensure_usage_schema,
)
from app.database.engine import async_engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """See app/tests/test_llm_settings.py for why this is needed (event-loop-per-test
    vs a module-level asyncpg connection pool)."""
    yield
    await async_engine.dispose()


async def _first_workspace_id() -> str:
    async with async_engine.connect() as conn:
        row = (await conn.execute(text("SELECT id FROM workspaces ORDER BY id LIMIT 1"))).first()
    if row is None:
        pytest.skip("No workspace rows available to test against")
    return str(row[0])


async def _snapshot(ws_id: str) -> dict:
    async with async_engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                SELECT plan, max_docs, max_queries_per_day, max_storage_gb,
                       doc_count, query_count_today, storage_used_mb, query_count_reset_at
                FROM workspaces WHERE id = :wsid
            """),
                {"wsid": ws_id},
            )
        ).mappings().first()
    return dict(row)


async def _restore(ws_id: str, snap: dict) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
                UPDATE workspaces
                SET plan = :plan, max_docs = :max_docs, max_queries_per_day = :mqpd,
                    max_storage_gb = :msg, doc_count = :doc_count,
                    query_count_today = :qct, storage_used_mb = :sum,
                    query_count_reset_at = :reset_at
                WHERE id = :wsid
            """),
            {
                "plan": snap["plan"],
                "max_docs": snap["max_docs"],
                "mqpd": snap["max_queries_per_day"],
                "msg": snap["max_storage_gb"],
                "doc_count": snap["doc_count"],
                "qct": snap["query_count_today"],
                "sum": snap["storage_used_mb"],
                "reset_at": snap["query_count_reset_at"],
                "wsid": ws_id,
            },
        )


@pytest.mark.asyncio
async def test_schema_creation_idempotent():
    await ensure_usage_schema()
    await ensure_usage_schema()


@pytest.mark.asyncio
async def test_doc_limit_check_at_and_over_threshold():
    ws_id = await _first_workspace_id()
    await ensure_usage_schema()
    snap = await _snapshot(ws_id)
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("UPDATE workspaces SET max_docs = 5, doc_count = 4 WHERE id = :wsid"), {"wsid": ws_id}
            )
        ok, _ = await check_doc_limit(ws_id)
        assert ok is True

        async with async_engine.begin() as conn:
            await conn.execute(
                text("UPDATE workspaces SET doc_count = 5 WHERE id = :wsid"), {"wsid": ws_id}
            )
        ok, msg = await check_doc_limit(ws_id)
        assert ok is False
        assert "limit reached" in msg.lower()
    finally:
        await _restore(ws_id, snap)


@pytest.mark.asyncio
async def test_storage_limit_check():
    ws_id = await _first_workspace_id()
    snap = await _snapshot(ws_id)
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("UPDATE workspaces SET max_storage_gb = 1.0, storage_used_mb = 1000 WHERE id = :wsid"),
                {"wsid": ws_id},
            )
        # 1000 + 20 = 1020MB < 1024MB (1GB) -> ok
        ok, _ = await check_storage_limit(ws_id, incoming_mb=20)
        assert ok is True
        # 1000 + 100 = 1100MB > 1024MB -> rejected
        ok, msg = await check_storage_limit(ws_id, incoming_mb=100)
        assert ok is False
        assert "storage limit" in msg.lower()
    finally:
        await _restore(ws_id, snap)


@pytest.mark.asyncio
async def test_query_limit_lazy_daily_reset():
    ws_id = await _first_workspace_id()
    await ensure_usage_schema()
    snap = await _snapshot(ws_id)
    try:
        stale_date = date.today() - timedelta(days=2)
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE workspaces
                    SET max_queries_per_day = 10, query_count_today = 10, query_count_reset_at = :stale
                    WHERE id = :wsid
                """),
                {"stale": stale_date, "wsid": ws_id},
            )

        # Before reset this would fail (10 >= 10) — the lazy reset inside check_query_limit
        # should zero the stale counter first, so the check passes.
        ok, _ = await check_query_limit(ws_id)
        assert ok is True

        async with async_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT query_count_today, query_count_reset_at FROM workspaces WHERE id = :wsid"),
                    {"wsid": ws_id},
                )
            ).mappings().first()
        assert row["query_count_today"] == 0
        assert row["query_count_reset_at"] == date.today()
    finally:
        await _restore(ws_id, snap)


@pytest.mark.asyncio
async def test_query_limit_not_reset_when_current():
    ws_id = await _first_workspace_id()
    snap = await _snapshot(ws_id)
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE workspaces
                    SET max_queries_per_day = 10, query_count_today = 10, query_count_reset_at = CURRENT_DATE
                    WHERE id = :wsid
                """),
                {"wsid": ws_id},
            )
        # Reset marker is already today — should NOT reset, so the limit still applies.
        ok, msg = await check_query_limit(ws_id)
        assert ok is False
        assert "daily query limit" in msg.lower()
    finally:
        await _restore(ws_id, snap)


@pytest.mark.asyncio
async def test_update_subscription_syncs_plan_limits():
    ws_id = await _first_workspace_id()
    snap = await _snapshot(ws_id)
    try:
        await update_subscription(ws_id, plan="business", subscription_id="sub_test_usage", status="active")
        async with async_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT plan, max_docs, max_queries_per_day, max_storage_gb FROM workspaces WHERE id = :wsid"),
                    {"wsid": ws_id},
                )
            ).mappings().first()
        expected = PLAN_REGISTRY["business"]
        assert row["plan"] == "business"
        assert row["max_docs"] == expected["max_docs"]
        assert row["max_queries_per_day"] == expected["max_queries_per_day"]
        assert row["max_storage_gb"] == expected["max_storage_gb"]
    finally:
        await _restore(ws_id, snap)


@pytest.mark.asyncio
async def test_update_subscription_leaves_limits_alone_for_unlimited_plan():
    """Enterprise has max_docs=None (unlimited) in PLAN_REGISTRY — writing that into the
    NOT NULL Workspace.max_docs column would fail, so update_subscription() must leave
    the existing limit columns untouched for such plans."""
    ws_id = await _first_workspace_id()
    snap = await _snapshot(ws_id)
    try:
        async with async_engine.begin() as conn:
            await conn.execute(text("UPDATE workspaces SET max_docs = 777 WHERE id = :wsid"), {"wsid": ws_id})
        await update_subscription(ws_id, plan="enterprise", subscription_id="sub_ent", status="active")
        async with async_engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT plan, max_docs FROM workspaces WHERE id = :wsid"), {"wsid": ws_id})
            ).mappings().first()
        assert row["plan"] == "enterprise"
        assert row["max_docs"] == 777  # untouched
    finally:
        await _restore(ws_id, snap)


def test_usage_route_requires_auth():
    assert client.get("/api/v1/billing/usage").status_code in (401, 403)


@pytest.mark.asyncio
async def test_middleware_blocks_query_over_daily_limit():
    """End-to-end: an authenticated request to /api/v1/query should get a 429 from
    UsageLimiterMiddleware once query_count_today >= max_queries_per_day."""
    ws_id = await _first_workspace_id()
    snap = await _snapshot(ws_id)
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE workspaces
                    SET max_queries_per_day = 1, query_count_today = 1, query_count_reset_at = CURRENT_DATE
                    WHERE id = :wsid
                """),
                {"wsid": ws_id},
            )

        from app.auth.jwt_handler import create_access_token

        token = create_access_token(user_id=str(uuid.uuid4()), email="test@example.com", workspace_id=ws_id, role="editor")

        # TestClient drives the ASGI app on its own internal event loop, distinct from
        # this test function's pytest-asyncio loop — dispose the pool first so the
        # middleware's DB check doesn't try to reuse a connection bound to the wrong loop
        # (same class of issue documented in test_llm_settings.py).
        await async_engine.dispose()
        response = client.post(
            "/api/v1/query",
            json={"question": "does the middleware block this?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 429
        body = response.json()
        assert body["error"] == "workspace_limit_exceeded"
        assert "upgrade_url" in body
    finally:
        # Same loop-boundary reasoning as above — TestClient's request just ran the
        # engine on its own internal loop, so dispose again before this test's own loop
        # reuses it for cleanup.
        await async_engine.dispose()
        await _restore(ws_id, snap)
