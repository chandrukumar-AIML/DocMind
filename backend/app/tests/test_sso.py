"""Tests for SSO (OIDC): per-workspace config, state tokens, route gating, discovery failures.

No real IdP account exists — Authlib's actual network calls (discovery, token exchange)
are exercised against example.okta.com's real (but generic/templated) discovery response
where noted, or mocked/expected-to-fail otherwise. Nothing here depends on a live customer
Okta/Azure tenant.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt_handler import create_sso_state_token, verify_sso_state_token
from app.core.workspace_sso_config import (
    delete_workspace_sso_config,
    ensure_workspace_sso_schema,
    get_workspace_sso_config,
    get_workspace_sso_config_by_slug,
    get_workspace_sso_config_masked,
    upsert_workspace_sso_config,
)
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
async def _dispose_engine_pool_between_tests():
    """See app/tests/test_llm_settings.py for why this is needed (event-loop-per-test
    vs a module-level asyncpg connection pool)."""
    yield
    from app.database.engine import async_engine

    await async_engine.dispose()


def test_sso_state_token_round_trip():
    token = create_sso_state_token(workspace_id="ws-123", code_verifier="verifier-abc", nonce="nonce-xyz")
    claims = verify_sso_state_token(token)
    assert claims["workspace_id"] == "ws-123"
    assert claims["code_verifier"] == "verifier-abc"
    assert claims["nonce"] == "nonce-xyz"
    assert claims["type"] == "sso_state"


def test_sso_state_token_rejects_tampering():
    token = create_sso_state_token(workspace_id="ws-123", code_verifier="verifier-abc", nonce="nonce-xyz")
    tampered = token[:-5] + "XXXXX"
    assert verify_sso_state_token(tampered) is None


def test_sso_state_token_rejects_expired():
    with patch("app.auth.jwt_handler._SSO_STATE_TTL_MINUTES", -1):
        token = create_sso_state_token(workspace_id="ws-123", code_verifier="v", nonce="n")
    assert verify_sso_state_token(token) is None


@pytest.mark.asyncio
async def test_schema_creation_idempotent():
    await ensure_workspace_sso_schema()
    await ensure_workspace_sso_schema()


@pytest.mark.asyncio
async def test_users_hashed_password_is_nullable():
    from sqlalchemy import text as sql_text

    from app.database.engine import async_engine

    await ensure_workspace_sso_schema()
    async with async_engine.connect() as conn:
        result = await conn.execute(
            sql_text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='hashed_password'"
            )
        )
        assert result.scalar() == "YES"


@pytest.mark.asyncio
async def test_sso_config_round_trip():
    from app.database.engine import async_engine
    from sqlalchemy import text as sql_text

    async with async_engine.connect() as conn:
        row = (await conn.execute(sql_text("SELECT id, slug FROM workspaces ORDER BY id LIMIT 1"))).first()
    if row is None:
        pytest.skip("No workspace rows available to test against")
    ws_id, ws_slug = str(row[0]), row[1]

    await ensure_workspace_sso_schema()
    try:
        result = await upsert_workspace_sso_config(
            ws_id, client_id="test-client-id", client_secret="super-secret-value", issuer="https://test.okta.com"
        )
        assert result["client_id"] == "test-client-id"
        assert result["client_secret_masked"].endswith("alue")
        assert "super-secret-value" not in str(result)

        config = await get_workspace_sso_config(ws_id)
        assert config.client_secret == "super-secret-value"
        assert config.issuer == "https://test.okta.com"

        by_slug = await get_workspace_sso_config_by_slug(ws_slug)
        assert by_slug is not None
        assert by_slug.workspace_id == ws_id

        masked = await get_workspace_sso_config_masked(ws_id)
        assert masked["client_secret_masked"] != "super-secret-value"

        deleted = await delete_workspace_sso_config(ws_id)
        assert deleted is True
        assert await get_workspace_sso_config(ws_id) is None
    finally:
        await delete_workspace_sso_config(ws_id)


def test_sso_config_routes_require_auth():
    assert client.get("/api/v1/sso/config").status_code in (401, 403)
    assert client.put(
        "/api/v1/sso/config",
        json={"client_id": "x", "client_secret": "y", "issuer": "https://x.okta.com"},
    ).status_code in (401, 403)
    assert client.delete("/api/v1/sso/config").status_code in (401, 403)


def test_authorize_404s_when_no_sso_configured():
    response = client.get("/api/v1/sso/authorize", params={"workspace_slug": "no-such-workspace-slug-xyz"})
    assert response.status_code == 404


def test_authorize_is_public_no_auth_required():
    # 404 (not configured) rather than 401/403 proves this route doesn't require auth
    response = client.get("/api/v1/sso/authorize", params={"workspace_slug": "no-such-workspace-slug-xyz"})
    assert response.status_code != 401
    assert response.status_code != 403


def test_callback_rejects_invalid_state():
    response = client.get(
        "/api/v1/sso/callback",
        params={"code": "fake-code", "state": "not-a-valid-signed-state"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_callback_rejects_expired_state():
    with patch("app.auth.jwt_handler._SSO_STATE_TTL_MINUTES", -1):
        expired_state = create_sso_state_token(workspace_id="ws-1", code_verifier="v", nonce="n")
    response = client.get(
        "/api/v1/sso/callback",
        params={"code": "fake-code", "state": expired_state},
        follow_redirects=False,
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_callback_404s_when_workspace_sso_config_removed_mid_flow():
    """State token references a workspace_id with no active SSO config (e.g. an admin
    deleted it between /authorize and /callback) — should 404, not crash."""
    state = create_sso_state_token(workspace_id="00000000-0000-0000-0000-000000000000", code_verifier="v", nonce="n")
    response = client.get(
        "/api/v1/sso/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 404
