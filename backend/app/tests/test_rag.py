import pytest
from unittest.mock import Mock, patch, MagicMock
from app.rag.chain import AdvancedRAGChain, Citation
from app.rag.hybrid_search import HybridSearcher, _get_bm25_cache_path

def test_rag_chain_error_handling():
    """Verify RAG chain handles errors gracefully with safe fallbacks."""
    chain = AdvancedRAGChain()
    
    # Test fallback answer with empty reranked list
    answer = chain._generate_fallback_answer([], "test question")
    assert "couldn't find relevant" in answer.lower()
    
    # Test fallback answer with valid docs but citation error
    mock_doc = Mock()
    mock_doc.page_content = "Test content"
    mock_doc.metadata = {"source_file": "test.pdf", "page_number": 0, "block_type": "paragraph"}
    
    with patch("app.rag.chain.format_citation", side_effect=Exception("Mock error")):
        answer = chain._generate_fallback_answer([(mock_doc, 0.9)], "test")
        # Should return safe fallback message, not crash
        assert "error" in answer.lower() or "try again" in answer.lower()

def test_context_building_error_handling():
    """Verify context building handles errors with safe fallback."""
    chain = AdvancedRAGChain()
    
    # Test with empty reranked list
    context, citations = chain._build_context_and_citations([])
    assert "<document_context>" in context
    assert citations == []
    
    # Test with valid docs
    mock_doc = Mock()
    mock_doc.page_content = "Test content"
    mock_doc.metadata = {
        "source_file": "test.pdf",
        "page_number": 0,
        "block_type": "paragraph",
        "chunk_id": "abc123"
    }
    
    context, citations = chain._build_context_and_citations([(mock_doc, 0.9)])
    assert "<document_context>" in context
    assert len(citations) == 1
    assert citations[0].source_file == "test.pdf"

def test_hybrid_search_cache_config():
    """Verify BM25 cache path is configurable."""
    with patch("app.rag.hybrid_search.get_settings") as mock_settings:
        # Test default fallback
        mock_settings.return_value.bm25_cache_path = None
        default_path = _get_bm25_cache_path()
        assert str(default_path).endswith(".cache/bm25_index.pkl")
        
        # Test custom path
        mock_settings.return_value.bm25_cache_path = "/tmp/bm25_test.pkl"
        custom_path = _get_bm25_cache_path()
        assert str(custom_path) == "/tmp/bm25_test.pkl"

def test_citation_model_conversion():
    """Verify Citation.to_dict() works correctly."""
    citation = Citation(
        source_file="test.pdf",
        page_number=3,
        block_type="table",
        chunk_text="Revenue: $1M" * 100,  # Long text
        rerank_score=0.89456,
        chunk_id="chunk_123"
    )
    api_dict = citation.to_dict()
    
    assert api_dict["page_number"] == 4  # 3 + 1 (1-indexed for UI)
    assert len(api_dict["chunk_text"]) <= 203  # 200 + "..."
    assert api_dict["rerank_score"] == 0.8946  # Rounded to 4 decimals
    assert api_dict["chunk_id"] == "chunk_123"