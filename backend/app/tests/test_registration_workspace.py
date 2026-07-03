"""Tests for self-serve registration creating an isolated workspace per signup."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database.engine import async_engine
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """See app/tests/test_llm_settings.py for why this is needed (event-loop-per-test
    vs a module-level asyncpg connection pool)."""
    yield
    await async_engine.dispose()


@pytest.fixture(autouse=True)
async def _reset_registration_rate_limit():
    """TestClient always reports client.host as 'testclient', so the register()
    endpoint's per-IP rate limit (5/hour) persists across separate pytest runs via the
    shared cache backend and would otherwise make this file flaky when re-run."""
    from app.cache import get_cache

    cache = await get_cache()
    await cache.delete("rate:register:ip:testclient")
    for domain in ("example.com",):
        await cache.delete(f"rate:register:domain:{domain}")
    yield


async def _cleanup(email: str, workspace_id: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM workspace_members WHERE workspace_id = :wsid"), {"wsid": workspace_id}
        )
        await conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
        await conn.execute(text("DELETE FROM workspaces WHERE id = :wsid"), {"wsid": workspace_id})


@pytest.mark.asyncio
async def test_register_creates_isolated_workspace():
    email = f"acme-{uuid.uuid4().hex[:8]}@example.com"
    await async_engine.dispose()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Secure@Pass1!",
            "display_name": "Acme Founder",
            "workspace_name": "Acme Corp",
        },
    )
    await async_engine.dispose()
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["workspace_id"]
    assert data["workspace_slug"].startswith("acme-corp-")

    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                        SELECT wm.role, w.name, w.slug
                        FROM workspace_members wm
                        JOIN workspaces w ON w.id = wm.workspace_id
                        JOIN users u ON u.id = wm.user_id
                        WHERE u.email = :email
                    """),
                    {"email": email},
                )
            )
            .mappings()
            .fetchone()
        )
    try:
        assert row is not None
        assert row["role"] == "workspace_admin"
        assert row["name"] == "Acme Corp"
        assert row["slug"] == data["workspace_slug"]
    finally:
        await _cleanup(email, data["workspace_id"])
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_register_without_workspace_name_derives_default():
    email = f"solo-{uuid.uuid4().hex[:8]}@example.com"
    await async_engine.dispose()
    resp = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "Secure@Pass1!",
            "display_name": "Solo Founder",
        },
    )
    await async_engine.dispose()
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["workspace_slug"].startswith("solo-founder-s-workspace-")
    await _cleanup(email, data["workspace_id"])
    await async_engine.dispose()


@pytest.mark.asyncio
async def test_two_registrations_same_company_name_get_different_workspaces():
    email_a = f"dup-a-{uuid.uuid4().hex[:8]}@example.com"
    email_b = f"dup-b-{uuid.uuid4().hex[:8]}@example.com"

    await async_engine.dispose()
    resp_a = client.post(
        "/api/v1/auth/register",
        json={
            "email": email_a,
            "password": "Secure@Pass1!",
            "display_name": "Person A",
            "workspace_name": "Shared Co",
        },
    )
    await async_engine.dispose()

    await async_engine.dispose()
    resp_b = client.post(
        "/api/v1/auth/register",
        json={
            "email": email_b,
            "password": "Secure@Pass1!",
            "display_name": "Person B",
            "workspace_name": "Shared Co",
        },
    )
    await async_engine.dispose()

    assert resp_a.status_code == 201, resp_a.text
    assert resp_b.status_code == 201, resp_b.text
    data_a, data_b = resp_a.json(), resp_b.json()

    assert data_a["workspace_id"] != data_b["workspace_id"]
    assert data_a["workspace_slug"] != data_b["workspace_slug"]

    await _cleanup(email_a, data_a["workspace_id"])
    await _cleanup(email_b, data_b["workspace_id"])
    await async_engine.dispose()
