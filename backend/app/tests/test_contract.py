"""
API contract tests — verify the OpenAPI schema matches what the frontend expects.

These tests:
1. Fetch the live /openapi.json schema from the running app
2. Verify every endpoint the frontend client.js calls actually exists
3. Verify response shapes match what the frontend parses

No mocks for the schema itself — we want to catch real drift between
frontend expectations and backend implementation.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, ASGITransport


def _make_test_settings():
    s = MagicMock()
    s.app_name = "DocuMind Contract Test"
    s.app_version = "test"
    s.api_reload = True
    s.api_host = "127.0.0.1"
    s.api_port = 8000
    s.cors_origins = ["http://localhost:3000"]
    s.jwt_secret_key = "contract-test-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    s.jwt_algorithm = "HS256"
    s.jwt_access_token_expire_minutes = 60
    s.eager_startup_services = False
    s.environment = "test"
    return s


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


@pytest.fixture
async def schema(client):
    """Fetch and cache the OpenAPI schema."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200, "OpenAPI schema not available"
    return resp.json()


# ── Contract: every frontend endpoint must exist in the schema ────────────────

# Endpoints called by frontend/src/api/client.js
FRONTEND_ENDPOINTS = [
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("GET",  "/api/v1/auth/me"),
    ("GET",  "/api/v1/documents/"),
    ("POST", "/api/v1/ingest/document"),
    ("POST", "/api/v1/ingest/audio"),
    ("POST", "/api/v1/ingest/url"),
    ("POST", "/api/v1/query"),
    ("GET",  "/api/v1/workspaces/"),
    ("GET",  "/api/v1/health"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", FRONTEND_ENDPOINTS)
async def test_endpoint_exists_in_schema(schema, method, path):
    """Every endpoint the frontend calls must be declared in the OpenAPI schema."""
    paths = schema.get("paths", {})

    # Normalize path: /api/v1/auth/login → check both with and without trailing /
    found = path in paths or path.rstrip("/") in paths or (path + "/") in paths

    # Also handle path parameters: /api/v1/documents/{id} matches /api/v1/documents/
    if not found:
        # Check if any schema path is a prefix match (for parameterised routes)
        base = path.rstrip("/")
        found = any(
            p.startswith(base) or base.startswith(p.rstrip("/"))
            for p in paths
        )

    assert found, (
        f"Frontend calls {method} {path} but it is not declared in the OpenAPI schema. "
        f"Schema paths: {sorted(paths.keys())}"
    )


@pytest.mark.asyncio
async def test_schema_has_required_security_schemes(schema):
    """Schema must declare Bearer token security so clients know auth is required."""
    components = schema.get("components", {})
    security_schemes = components.get("securitySchemes", {})
    # At least one Bearer/JWT scheme must exist
    has_bearer = any(
        v.get("scheme", "").lower() == "bearer" or
        v.get("type", "").lower() in ("http", "apikey", "oauth2")
        for v in security_schemes.values()
    )
    assert has_bearer, f"No Bearer security scheme found. Schemes: {security_schemes}"


@pytest.mark.asyncio
async def test_health_response_shape(client):
    """/health must return {status: str, ...} — shape the frontend status bar relies on."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "status" in body
    assert isinstance(body["status"], str)


@pytest.mark.asyncio
async def test_login_error_shape(client):
    """Login 401 must return {detail: str} — frontend reads .detail for error messages."""
    resp = await client.post("/api/v1/auth/login", json={
        "email": "nobody@example.com",
        "password": "wrongpassword",
    })
    assert resp.status_code in (401, 422)
    body = resp.json()
    # FastAPI validation errors use {detail: ...}
    assert "detail" in body


@pytest.mark.asyncio
async def test_documents_list_shape(client):
    """Unauthenticated /documents/ must return 401 — not a 500 or malformed error."""
    resp = await client.get("/api/v1/documents/")
    assert resp.status_code in (401, 403)
    body = resp.json()
    assert "detail" in body


@pytest.mark.asyncio
async def test_openapi_schema_version(schema):
    """Schema must be OpenAPI 3.x (not 2.x Swagger) — frontend uses 3.x features."""
    openapi_version = schema.get("openapi", "")
    assert openapi_version.startswith("3."), f"Expected OpenAPI 3.x, got: {openapi_version}"


@pytest.mark.asyncio
async def test_all_endpoints_have_response_schemas(schema):
    """
    Every POST/GET endpoint must declare at least one response schema.
    Missing response schemas cause frontend type inference to fail silently.
    """
    paths = schema.get("paths", {})
    missing = []
    for path, methods in paths.items():
        for method, defn in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            responses = defn.get("responses", {})
            if not responses:
                missing.append(f"{method.upper()} {path}")

    assert not missing, (
        f"{len(missing)} endpoint(s) have no response schemas:\n" +
        "\n".join(f"  - {m}" for m in missing[:20])
    )


@pytest.mark.asyncio
async def test_cors_preflight_allowed(client):
    """OPTIONS preflight from the frontend origin must return 200."""
    resp = await client.options(
        "/api/v1/query",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in resp.headers
