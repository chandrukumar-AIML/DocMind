from pathlib import Path
from unittest.mock import Mock
from app.rag.chain import AdvancedRAGChain, Citation
from app.rag.hybrid_search import _get_bm25_cache_path


def test_rag_chain_fallback_empty():
    """Verify RAG chain returns safe fallback with no relevant documents."""
    chain = AdvancedRAGChain()

    answer = chain._generate_fallback_answer([], "test question")
    assert "couldn't find relevant" in answer.lower()


def test_rag_chain_fallback_with_docs():
    """Verify RAG chain produces extractive answer when docs are available."""
    chain = AdvancedRAGChain()

    mock_doc = Mock()
    mock_doc.page_content = "Revenue grew 23% year-over-year to $142M in FY2024."
    mock_doc.metadata = {
        "source_file": "annual_report.pdf",
        "page_number": 0,
        "block_type": "paragraph",
    }

    answer = chain._generate_fallback_answer([(mock_doc, 0.9)], "What is revenue?")
    # Should return extractive answer (not empty, not crash)
    assert isinstance(answer, str)
    assert len(answer) > 0


def test_citation_to_dict():
    """Verify Citation.to_dict() computes derived fields correctly."""
    citation = Citation(
        source_file="test.pdf",
        page_number=3,
        block_type="table",
        chunk_text="Revenue: $1M" * 100,  # Long text — must be truncated
        rerank_score=0.89456,
        chunk_id="chunk_123",
    )
    api_dict = citation.to_dict()

    assert api_dict["page_number"] == 4  # 3 + 1 (1-indexed for UI)
    assert len(api_dict["chunk_text"]) <= 203  # 200 + "..."
    assert api_dict["rerank_score"] == 0.8946  # Rounded to 4 decimals
    assert api_dict["chunk_id"] == "chunk_123"


def test_hybrid_search_cache_config():
    """Verify BM25 cache path is configurable (OS-agnostic comparison)."""
    from unittest.mock import patch
    import os

    with patch("app.rag.hybrid_search.get_settings") as mock_settings:
        # Default fallback — compare parts to avoid Windows vs POSIX separator issues
        mock_settings.return_value.bm25_cache_path = None
        default_path = _get_bm25_cache_path()
        assert default_path == Path(".cache") / "bm25_index.pkl"

        # Custom path — compare with a Path object (normalises separators)
        custom_raw = os.path.join("tmp", "bm25_test.pkl")
        mock_settings.return_value.bm25_cache_path = custom_raw
        custom_path = _get_bm25_cache_path()
        assert custom_path == Path(custom_raw)
