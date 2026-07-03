"""Tests for API-key authentication: ApiKeyAuthMiddleware + get_current_user fallback."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.auth.dependencies import _api_key_user_from_state
from app.auth.models import UserRole
from app.database.engine import async_engine
from app.main import app

client = TestClient(app)

_WS_UUID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """See app/tests/test_llm_settings.py for why this is needed (event-loop-per-test
    vs a module-level asyncpg connection pool)."""
    yield
    await async_engine.dispose()


# ── Unit: synthetic-user construction + role mapping ─────────────────────────


def test_returns_none_when_no_api_key_state():
    req = SimpleNamespace(state=SimpleNamespace())
    assert _api_key_user_from_state(req, "corr") is None


def test_write_scope_maps_to_editor():
    state = SimpleNamespace(
        api_key_workspace_id=_WS_UUID, api_key_scopes=["read", "write"], api_key_id="key-1"
    )
    user = _api_key_user_from_state(SimpleNamespace(state=state), "corr")
    assert user is not None
    assert user.role == UserRole.EDITOR.value
    assert user.workspace_id == _WS_UUID
    assert user.can_write() is True
    assert user.user_id == "apikey:key-1"


def test_read_only_scope_maps_to_viewer():
    state = SimpleNamespace(api_key_workspace_id=_WS_UUID, api_key_scopes=["read"], api_key_id="key-2")
    user = _api_key_user_from_state(SimpleNamespace(state=state), "corr")
    assert user.role == UserRole.VIEWER.value
    assert user.can_write() is False


# ── Integration: full middleware → get_current_user → route chain ────────────


async def _seed_key(scopes: list[str]):
    from app.core.apikey_manager import ensure_apikey_schema, create_api_key

    await ensure_apikey_schema()
    async with async_engine.connect() as conn:
        ws = (await conn.execute(text("SELECT id::text FROM workspaces ORDER BY created_at LIMIT 1"))).first()
        usr = (await conn.execute(text("SELECT id::text FROM users LIMIT 1"))).first()
    if ws is None or usr is None:
        pytest.skip("No workspace/user rows available to test against")
    result = await create_api_key(workspace_id=ws[0], name="pytest-key", scopes=scopes, created_by=usr[0])
    return ws[0], result["api_key"], result["key_id"]


async def _delete_key(key_id: str):
    async with async_engine.begin() as conn:
        await conn.execute(text("DELETE FROM api_keys WHERE id = :kid"), {"kid": key_id})


@pytest.mark.asyncio
async def test_valid_api_key_authenticates_protected_route():
    ws_id, raw_key, key_id = await _seed_key(["read", "write"])
    try:
        await async_engine.dispose()
        resp = client.get(
            f"/api/v1/documents?workspace_id={ws_id}",
            headers={"Authorization": f"ApiKey {raw_key}"},
        )
        await async_engine.dispose()
        assert resp.status_code == 200, resp.text
    finally:
        await _delete_key(key_id)
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_invalid_api_key_rejected():
    await async_engine.dispose()
    resp = client.get(
        "/api/v1/documents?workspace_id=default",
        headers={"Authorization": "ApiKey dmk_this_is_not_a_real_key"},
    )
    await async_engine.dispose()
    assert resp.status_code == 401
    assert resp.json().get("error") == "invalid_api_key"


@pytest.mark.asyncio
async def test_no_credentials_still_rejected():
    await async_engine.dispose()
    resp = client.get("/api/v1/documents?workspace_id=default")
    await async_engine.dispose()
    assert resp.status_code == 401
