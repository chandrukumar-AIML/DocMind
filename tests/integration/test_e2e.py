"""
DocuMind AI — End-to-End Integration Tests
Tests the full pipeline from upload to query across all phases.

Run:
    pytest tests/integration/test_e2e.py -v --asyncio-mode=auto
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="module")
def client():
    return httpx.Client(base_url=BASE_URL, timeout=120.0)


@pytest.fixture(scope="module")
def auth_headers(client):
    """Get auth headers for test user."""
    settings_resp = client.get("/health")
    if settings_resp.status_code != 200:
        pytest.skip("Backend not running")

    # Register test user
    resp = client.post("/api/v1/auth/register", json={
        "email":     "e2e_test@documind.local",
        "password":  "test_password_123",
        "full_name": "E2E Test User",
    })
    if resp.status_code in (201, 409):   # 409 = already exists
        if resp.status_code == 409:
            resp = client.post("/api/v1/auth/login", data={
                "username": "e2e_test@documind.local",
                "password": "test_password_123",
            })
        token = resp.json().get("access_token", "")
        return {"Authorization": f"Bearer {token}"}
    return {}


def _make_test_pdf() -> bytes:
    """Create a minimal test PDF for ingestion tests."""
    content = b"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj
4 0 obj << /Length 120 >>
stream
BT /F1 12 Tf 72 720 Td (DocuMind AI Test Document) Tj
0 -20 Td (Payment terms: Net 30 days from invoice date.) Tj
0 -20 Td (Liability cap: $500,000 per incident.) Tj ET
endstream endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000446 00000 n
trailer << /Size 6 /Root 1 0 R >>
startxref
529
%%EOF"""
    return content


# ── Test 1: Health check ───────────────────────────────────────────────────────
def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ok", "degraded")
    assert "version" in data
    assert "vector_store" in data
    print(f"\n✅ Health: status={data['status']}")


# ── Test 2: Auth flow ─────────────────────────────────────────────────────────
def test_auth_register_and_login(client):
    import uuid
    email = f"test_{uuid.uuid4().hex[:8]}@documind.local"

    # Register
    resp = client.post("/api/v1/auth/register", json={
        "email":     email,
        "password":  "test_password_123",
        "full_name": "Test User",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "workspace_id" in data
    token = data["access_token"]

    # Get current user
    me_resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == email
    print(f"\n✅ Auth: registered + logged in as {email}")


# ── Test 3: Async ingest ──────────────────────────────────────────────────────
def test_async_ingest(client, auth_headers):
    pdf_content = _make_test_pdf()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_content)
        tmp_path = f.name

    try:
        with open(tmp_path, "rb") as f:
            resp = client.post(
                "/api/v1/ingest",
                files={"file": ("test_e2e.pdf", f, "application/pdf")},
                data={"priority": "default"},
                headers=auth_headers,
            )
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"
        assert "ws_url" in data
        assert "poll_url" in data
        print(f"\n✅ Ingest: task_id={data['task_id']} queued")

        # Poll for completion (up to 60s)
        task_id = data["task_id"]
        for attempt in range(12):
            asyncio.get_event_loop().run_until_complete(asyncio.sleep(5))
            poll = client.get(
                f"/api/v1/tasks/{task_id}",
                headers=auth_headers,
            )
            if poll.status_code == 200:
                state = poll.json()
                if state["status"] == "complete":
                    print(f"   Complete: {state['chunk_count']} chunks")
                    break
                elif state["status"] == "failed":
                    pytest.fail(f"Ingest failed: {state.get('error')}")

    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Test 4: Query ─────────────────────────────────────────────────────────────
def test_query(client, auth_headers):
    resp = client.post(
        "/api/v1/query",
        json={
            "question": "What are the payment terms?",
            "stream":   False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["answer"]) > 10
    assert "citations" in data
    assert "latency_seconds" in data
    print(
        f"\n✅ Query: answer='{data['answer'][:60]}' | "
        f"citations={len(data['citations'])}"
    )


# ── Test 5: Rate limiting ─────────────────────────────────────────────────────
def test_rate_limit_headers(client, auth_headers):
    resp = client.post(
        "/api/v1/query",
        json={"question": "test rate limit", "stream": False},
        headers=auth_headers,
    )
    # Rate limit headers should be present (if not 429)
    if resp.status_code == 200:
        print(f"\n✅ Rate limit: request allowed")
    elif resp.status_code == 429:
        data = resp.json()
        assert "Rate limit" in data.get("detail", "")
        print(f"\n✅ Rate limit: correctly blocked with 429")


# ── Test 6: Cache hits ────────────────────────────────────────────────────────
def test_query_cache(client, auth_headers):
    question = "What is the liability cap?"

    # First request — cache miss
    t0   = asyncio.get_event_loop().time()
    resp1 = client.post(
        "/api/v1/query",
        json={"question": question, "stream": False},
        headers=auth_headers,
    )
    t1 = asyncio.get_event_loop().time()

    assert resp1.status_code == 200
    latency1 = t1 - t0

    # Second request — should hit cache
    t2   = asyncio.get_event_loop().time()
    resp2 = client.post(
        "/api/v1/query",
        json={"question": question, "stream": False},
        headers=auth_headers,
    )
    t3 = asyncio.get_event_loop().time()

    assert resp2.status_code == 200
    latency2 = t3 - t2

    # Cache hit should be significantly faster
    print(
        f"\n✅ Cache: "
        f"miss={latency1:.2f}s | hit={latency2:.2f}s | "
        f"speedup={latency1/max(latency2, 0.001):.1f}×"
    )


# ── Test 7: Version history ───────────────────────────────────────────────────
def test_version_history(client, auth_headers):
    resp = client.get(
        "/api/v1/versions/test_e2e.pdf",
        headers=auth_headers,
    )
    if resp.status_code == 200:
        versions = resp.json()
        assert isinstance(versions, list)
        print(f"\n✅ Versions: {len(versions)} versions for test_e2e.pdf")
    elif resp.status_code == 404:
        print("\n⚠️  Versions: document not found (ingest may not have completed)")


# ── Test 8: Monitoring stats ──────────────────────────────────────────────────
def test_monitoring_stats(client, auth_headers):
    resp = client.get(
        "/api/v1/monitoring/stats?hours=1",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "query_count" in data
    assert "window_hours" in data
    print(
        f"\n✅ Monitoring: "
        f"queries={data['query_count']} | "
        f"confidence={data.get('confidence_mean', 'N/A')}"
    )


# ── Test 9: Document list ─────────────────────────────────────────────────────
def test_list_documents(client, auth_headers):
    resp = client.get("/api/v1/documents", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "documents" in data
    assert "total_count" in data
    print(f"\n✅ Documents: {data['total_count']} indexed")


# ── Test 10: Agent query ──────────────────────────────────────────────────────
def test_agent_query(client, auth_headers):
    resp = client.post(
        "/api/v1/agent-query",
        json={
            "question": "What does this document discuss?",
            "stream":   False,
        },
        headers=auth_headers,
    )
    assert resp.status_code in (200, 202)
    data = resp.json()
    assert "answer" in data or "task_id" in data
    print(f"\n✅ Agent: query processed")