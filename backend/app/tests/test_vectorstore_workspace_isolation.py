"""Tests for per-workspace vector store isolation (Chroma collection/registry/parents
naming, FAISS index path, and VectorStoreManager wiring)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from app.core.workspace_utils import get_chroma_collection_name, get_faiss_index_path
from app.vectorstore.chroma_store import ChromaVectorStore


class _FakeEmbeddings:
    """Deterministic fake embedding function — avoids real API calls in tests."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t) % 7), 0.1, 0.2] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text) % 7), 0.1, 0.2]


@pytest.fixture
def fake_embeddings() -> _FakeEmbeddings:
    return _FakeEmbeddings()


def _make_chunk(text: str, source_file: str, chunk_id: str, parent_id: str = "") -> Document:
    meta = {
        "chunk_id": chunk_id,
        "source_file": source_file,
        "document_type": "txt",
        "language": "en",
        "page_number": 0,
        "block_type": "text",
        "ocr_confidence": 0.0,
        "chunk_type": "child",
        "ingest_timestamp": "2026-01-01T00:00:00+00:00",
        "char_count": len(text),
        "parent_id": parent_id,
    }
    return Document(page_content=text, metadata=meta)


# ── Unit: naming derivation ──────────────────────────────────────────────────


def test_chroma_default_collection_name_unchanged(tmp_path, fake_embeddings):
    store = ChromaVectorStore(fake_embeddings, persist_directory=str(tmp_path))
    assert store.registry_collection_name == "document_registry"
    assert store.parents_collection_name == "parents"


def test_chroma_explicit_collection_name_scopes_registry_and_parents(tmp_path, fake_embeddings):
    store = ChromaVectorStore(fake_embeddings, collection_name="docs_ws_a", persist_directory=str(tmp_path))
    assert store.registry_collection_name == "docs_ws_a_registry"
    assert store.parents_collection_name == "docs_ws_a_parents"


def test_get_faiss_index_path_distinct_per_workspace():
    ws_a = str(uuid.uuid4())
    ws_b = str(uuid.uuid4())
    path_a = get_faiss_index_path(ws_a)
    path_b = get_faiss_index_path(ws_b)
    assert path_a != path_b
    assert path_a.parent == path_b.parent  # same base folder as today's global default


def test_get_chroma_collection_name_format():
    ws_id = str(uuid.uuid4())
    assert get_chroma_collection_name(ws_id) == f"docs_{ws_id}"


# ── Core regression: cross-workspace isolation at the Chroma layer ─────────


def test_cross_workspace_isolation(tmp_path, fake_embeddings):
    ws_a = "ws-a"
    ws_b = "ws-b"
    store_a = ChromaVectorStore(
        fake_embeddings, collection_name=get_chroma_collection_name(ws_a), persist_directory=str(tmp_path)
    )
    store_b = ChromaVectorStore(
        fake_embeddings, collection_name=get_chroma_collection_name(ws_b), persist_directory=str(tmp_path)
    )

    chunk = _make_chunk("hello from workspace A", "a.txt", "chunk-a-1", parent_id="parent-a-1")
    parent = _make_chunk("full parent context for A", "a.txt", "parent-a-1")

    store_a.add_chunks([chunk])
    store_a.add_parent_chunks([parent])

    # Workspace A sees its own document; workspace B sees nothing.
    docs_a = store_a.list_documents()
    docs_b = store_b.list_documents()
    assert len(docs_a) == 1
    assert docs_a[0]["source_file"] == "a.txt"
    assert docs_b == []

    results_a = store_a.similarity_search("hello", k=5)
    results_b = store_b.similarity_search("hello", k=5)
    assert len(results_a) == 1
    assert results_b == []

    # Parent chunk isolation — not just the main collection.
    assert store_a.get_parent("parent-a-1") == "full parent context for A"
    assert store_b.get_parent("parent-a-1") is None

    # Delete from A never touches B (nothing to touch — proves no shared state).
    deleted = store_a.delete_document("a.txt")
    assert deleted == 1
    assert store_a.list_documents() == []
    assert store_b.list_documents() == []


def test_legacy_default_path_unaffected(tmp_path, fake_embeddings):
    """VectorStoreManager()/ChromaVectorStore() with no workspace_id must keep
    resolving to the exact legacy names — no regression for existing callers."""
    store = ChromaVectorStore(fake_embeddings, persist_directory=str(tmp_path))
    chunk = _make_chunk("legacy doc content", "legacy.txt", "chunk-legacy-1")
    store.add_chunks([chunk])

    docs = store.list_documents()
    assert len(docs) == 1
    assert store.registry_collection_name == "document_registry"


# ── Wiring: VectorStoreManager derives collection_name/index_path from workspace_id ──


def test_vector_store_manager_derives_workspace_scoped_names():
    with patch("app.vectorstore.embeddings.CachedOpenAIEmbeddings"), patch(
        "app.vectorstore.chroma_store.ChromaVectorStore"
    ) as mock_chroma_cls, patch("app.vectorstore.faiss_store.FAISSVectorStore") as mock_faiss_cls, patch(
        "app.config.get_settings"
    ):
        mock_faiss_instance = MagicMock()
        mock_faiss_instance._store = None
        mock_faiss_cls.return_value = mock_faiss_instance

        from app.vectorstore.store_manager import VectorStoreManager

        workspace_id = "11111111-1111-1111-1111-111111111111"
        VectorStoreManager(workspace_id=workspace_id)

        _, chroma_kwargs = mock_chroma_cls.call_args
        assert chroma_kwargs["collection_name"] == f"docs_{workspace_id}"

        _, faiss_kwargs = mock_faiss_cls.call_args
        assert str(workspace_id) in str(faiss_kwargs["index_path"])


def test_vector_store_manager_no_workspace_id_keeps_legacy_defaults():
    with patch("app.vectorstore.embeddings.CachedOpenAIEmbeddings"), patch(
        "app.vectorstore.chroma_store.ChromaVectorStore"
    ) as mock_chroma_cls, patch("app.vectorstore.faiss_store.FAISSVectorStore") as mock_faiss_cls, patch(
        "app.config.get_settings"
    ):
        mock_faiss_instance = MagicMock()
        mock_faiss_instance._store = None
        mock_faiss_cls.return_value = mock_faiss_instance

        from app.vectorstore.store_manager import VectorStoreManager

        VectorStoreManager()

        _, chroma_kwargs = mock_chroma_cls.call_args
        assert chroma_kwargs["collection_name"] is None

        _, faiss_kwargs = mock_faiss_cls.call_args
        assert faiss_kwargs["index_path"] is None
