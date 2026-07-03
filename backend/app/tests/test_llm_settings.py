"""Tests for per-workspace BYOK LLM settings: encryption, schema, resolution, routes."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.llm_providers import PROVIDER_REGISTRY
from app.core.workspace_llm_config import (
    delete_workspace_llm_config,
    ensure_workspace_llm_schema,
    get_workspace_llm_config,
    get_workspace_llm_config_masked,
    upsert_workspace_llm_config,
)
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """
    pytest-asyncio gives each async test its own event loop, but SQLAlchemy's
    async_engine connection pool is a module-level singleton — a pooled asyncpg
    connection created in one test's loop errors out ("Exception terminating
    connection") when reused from the next test's loop. Disposing after every test
    forces a fresh connection next time instead of reusing a stale, loop-bound one.
    """
    yield
    from app.database.engine import async_engine

    await async_engine.dispose()


def test_encrypt_decrypt_round_trip():
    plaintext = "gsk_super_secret_key_1234567890"
    ciphertext = encrypt_secret(plaintext)
    assert ciphertext != plaintext
    assert decrypt_secret(ciphertext) == plaintext


def test_decrypt_invalid_ciphertext_raises():
    with pytest.raises(ValueError):
        decrypt_secret("not-a-real-fernet-token")


def test_provider_registry_shape():
    assert "groq" in PROVIDER_REGISTRY
    assert "openai" in PROVIDER_REGISTRY
    for entry in PROVIDER_REGISTRY.values():
        assert "default_model" in entry
        assert "label" in entry


@pytest.mark.asyncio
async def test_schema_creation_idempotent():
    # Should not raise even when called twice (CREATE TABLE IF NOT EXISTS)
    await ensure_workspace_llm_schema()
    await ensure_workspace_llm_schema()


@pytest.mark.asyncio
async def test_upsert_get_delete_round_trip():
    # A random workspace_id — no FK enforcement issue since compliance_results-style
    # tables in this codebase don't all enforce FK either; workspace_llm_settings does
    # reference workspaces(id), so we skip if the DB doesn't have a matching row.
    from app.database.engine import async_engine
    from sqlalchemy import text as sql_text

    async with async_engine.connect() as conn:
        row = (await conn.execute(sql_text("SELECT id FROM workspaces LIMIT 1"))).first()
    if row is None:
        pytest.skip("No workspace rows available to test FK-constrained upsert against")
    ws_id = str(row[0])

    await ensure_workspace_llm_schema()
    try:
        result = await upsert_workspace_llm_config(ws_id, provider="groq", api_key="gsk_test_roundtrip")
        assert result["provider"] == "groq"
        assert result["model"] == PROVIDER_REGISTRY["groq"]["default_model"]
        assert result["api_key_masked"].endswith("trip")
        assert "gsk_test_roundtrip" not in str(result)

        config = await get_workspace_llm_config(ws_id)
        assert config is not None
        assert config.api_key == "gsk_test_roundtrip"
        assert config.provider == "groq"

        masked = await get_workspace_llm_config_masked(ws_id)
        assert masked["api_key_masked"] != "gsk_test_roundtrip"

        deleted = await delete_workspace_llm_config(ws_id)
        assert deleted is True

        assert await get_workspace_llm_config(ws_id) is None
    finally:
        await delete_workspace_llm_config(ws_id)


@pytest.mark.asyncio
async def test_upsert_rejects_unknown_provider():
    with pytest.raises(ValueError):
        await upsert_workspace_llm_config(str(uuid.uuid4()), provider="not-a-real-provider", api_key="x")


@pytest.mark.asyncio
async def test_get_llm_for_workspace_falls_back_to_platform_default():
    from app.core.llm_pool import get_llm, get_llm_for_workspace

    # Unconfigured/nonexistent workspace -> same behavior as global get_llm()
    llm = await get_llm_for_workspace(str(uuid.uuid4()), streaming=False)
    baseline = get_llm(streaming=False)
    assert type(llm) is type(baseline)


def test_llm_settings_routes_require_auth():
    # No Authorization header -> rejected before reaching business logic
    assert client.get("/api/v1/llm-settings").status_code in (401, 403)
    assert client.put("/api/v1/llm-settings", json={"provider": "groq", "api_key": "x"}).status_code in (401, 403)
    assert client.delete("/api/v1/llm-settings").status_code in (401, 403)
    assert client.post("/api/v1/llm-settings/test").status_code in (401, 403)


def test_llm_settings_providers_route_is_public():
    # Provider list is not workspace-scoped — no auth required
    response = client.get("/api/v1/llm-settings/providers")
    assert response.status_code == 200
    body = response.json()
    assert any(p["id"] == "groq" for p in body["providers"])
