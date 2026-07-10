"""
REST API smoke tests — covers the primary HTTP surface via httpx.AsyncClient.

These tests mock heavy dependencies (DB, vector store, LLM) so they run fast
in CI without requiring external services.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


def _make_test_settings():
    settings = MagicMock()
    settings.app_name = "DocuMind AI Test"
    settings.app_version = "test"
    settings.api_reload = True
    settings.api_host = "127.0.0.1"
    settings.api_port = 8000
    settings.cors_origins = ["http://localhost:3000"]
    settings.jwt_secret_key = "test-secret-key-that-is-long-enough-for-hs256-xxxxx"
    settings.jwt_algorithm = "HS256"
    settings.jwt_access_token_expire_minutes = 60
    settings.eager_startup_services = False
    settings.environment = "test"
    return settings


@pytest.fixture
def app():
    with (
        patch("app.config.get_settings", return_value=_make_test_settings()),
        patch("app.config.lazy_settings", _make_test_settings()),
        patch("app.database.engine.async_engine"),
        patch("app.database.migrations.apply_pending_repairs", new_callable=AsyncMock),
    ):
        from app.main import create_app
        return create_app()


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health_endpoint(client):
    """Health endpoint must return 200 with a status field."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert "status" in resp.json()


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """Root returns service metadata."""
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "service" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_docs_available_in_dev_mode(client):
    """Swagger docs are accessible when api_reload=True."""
    resp = await client.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unauthenticated_query_rejected(client):
    """Query endpoint must reject unauthenticated requests."""
    resp = await client.post(
        "/api/v1/query/stream",
        json={"question": "test", "workspace_id": "default"},
    )
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_unauthenticated_documents_rejected(client):
    """Documents list endpoint must require authentication."""
    resp = await client.get("/api/v1/documents")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unauthenticated_ingest_rejected(client):
    """Ingest upload must require authentication."""
    resp = await client.post("/api/v1/ingest/upload")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_nonexistent_route_returns_404(client):
    """Non-existent routes must return 404, not 500."""
    resp = await client.get("/api/v1/does-not-exist-at-all")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ready_endpoint(client):
    """Readiness probe endpoint should respond."""
    resp = await client.get("/ready")
    assert resp.status_code in (200, 503)


@pytest.mark.asyncio
async def test_cors_preflight(client):
    """CORS preflight should accept requests from whitelisted origins."""
    resp = await client.options(
        "/api/v1/query/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code in (200, 204)
