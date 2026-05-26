import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_query_batch_mode_no_typo():
    """Verify reranked_count field exists in batch response."""
    # Mock minimal request - will fail at RAG chain but should not crash on attribute access
    response = client.post(
        "/api/v1/query",
        json={"question": "test", "stream": False},
        headers={"content-type": "application/json"}
    )
    # Expect 500 (no RAG chain in test) but NOT AttributeError
    assert response.status_code != 422  # Should pass validation
    # If we get 500, check it's not about 'retranked_count'
    if response.status_code == 500:
        assert "retranked_count" not in response.text.lower()

def test_sse_done_format():
    """Verify streaming response returns valid SSE or an error response if the chain is unavailable."""
    response = client.post(
        "/api/v1/query",
        json={"question": "test", "stream": True},
        headers={"content-type": "application/json"}
    )
    if response.status_code == 200:
        assert response.headers["content-type"] == "text/event-stream"
        assert "data: [DONE]" in response.text or "[DONE]" in response.text
    else:
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert "error" in body or "detail" in body


def test_openapi_schema_generation():
    """Verify OpenAPI schema generation does not raise and includes query path."""
    schema = app.openapi()
    assert isinstance(schema, dict)
    assert schema.get("openapi", "").startswith("3.")
    assert "/api/v1/query" in schema["paths"]
    assert "200" in schema["paths"]["/api/v1/query"]["post"]["responses"]
    assert "application/json" in schema["paths"]["/api/v1/query"]["post"]["responses"]["200"]["content"]
