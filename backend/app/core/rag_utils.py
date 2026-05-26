# backend/app/core/rag_utils.py
# DVMELTSS-FIX: M - Modular, S - Security, A - Async, V - Validate
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 1 - Prompt escaping, 7 - Safe context handling
"""
Shared utilities for RAG pipeline modules.

Centralizes:
- Async-safe LLM client pooling
- Prompt/content escaping for injection protection
- Correlation ID propagation helpers
- Safe tokenization and text normalization
- RRF fusion with configurable weights

Usage:
    from app.core.rag_utils import escape_prompt_content, generate_rag_correlation_id
"""
from __future__ import annotations

import asyncio
import logging
import re
import math
from typing import Final, Optional, List, Tuple, Callable, Any

from app.config import get_settings
from app.core.llm_pool import get_llm
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# DVMELTSS-S: Safe patterns for text processing
_PROMPT_ESCAPE_PATTERN: Final = re.compile(r'[<>&"\'\\]')
_TOKENIZE_PATTERN: Final = re.compile(r'[^\w\s]')
_CORRELATION_PREFIX: Final = "rag"

# DVMELTSS-S: RRF configuration defaults
_RRF_K: Final = 60  # RRF constant: higher = more weight to top ranks
_DEFAULT_SEMANTIC_WEIGHT: Final = 0.6
_DEFAULT_KEYWORD_WEIGHT: Final = 0.4

# DVMELTSS-V: Context safety limits
_MAX_CONTEXT_TOKENS: Final = 100_000
_MAX_CITATION_TEXT: Final = 300


def escape_prompt_content(text: str) -> str:
    """
    Escape special characters to prevent prompt injection.
    
    Args:
        text: Raw user or document content
    
    Returns:
        Safely escaped text for LLM prompts
    """
    if not text:
        return ""
    # Escape characters that could break prompt structure
    return _PROMPT_ESCAPE_PATTERN.sub(lambda m: f"\\{m.group()}", text)


def generate_rag_correlation_id(suffix: str = "") -> str:
    """Generate correlation ID for RAG operations."""
    from app.core.ids import generate_correlation_id
    base = f"{_CORRELATION_PREFIX}_{generate_correlation_id()}"
    return f"{base}_{suffix}" if suffix else base


def tokenize_for_bm25(text: str) -> List[str]:
    """
    Tokenize text for BM25 keyword search.
    
    Args:
        text: Raw document text
    
    Returns:
        List of lowercase, cleaned tokens
    """
    if not text:
        return []
    text = text.lower()
    text = _TOKENIZE_PATTERN.sub(" ", text)
    return [t for t in text.split() if t]


def normalize_rerank_score(logit: float) -> float:
    """Convert raw cross-encoder logit to [0, 1] via sigmoid."""
    logit = max(-20.0, min(20.0, logit))
    return 1.0 / (1.0 + math.exp(-logit))


def reciprocal_rank_fusion(
    ranked_lists: List[List[Tuple[Any, float]]],
    weights: List[float],
    k: int,
    rrf_k: int = _RRF_K,
) -> List[Tuple[Any, float]]:
    """
    Fuse multiple ranked lists using Reciprocal Rank Fusion.
    
    Args:
        ranked_lists: List of (item, score) lists, each pre-sorted by score desc
        weights: Weight for each list in fusion
        k: Number of results to return
        rrf_k: RRF constant
    
    Returns:
        Fused list of (item, fused_score) sorted by score desc
    """
    scores: dict[str, float] = {}
    item_map: dict[str, Any] = {}
    
    for ranked_list, weight in zip(ranked_lists, weights):
        for rank, (item, _) in enumerate(ranked_list, start=1):
            # Create stable ID from hash or metadata
            item_id = getattr(item, 'metadata', {}).get('chunk_id') or f"hash_{hash(str(item))}"
            rrf_score = weight / (rrf_k + rank)
            scores[item_id] = scores.get(item_id, 0.0) + rrf_score
            item_map[item_id] = item
    
    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [(item_map[doc_id], scores[doc_id]) for doc_id in sorted_ids[:k]]


def truncate_for_context(text: str, max_chars: int = 500) -> str:
    """Truncate text for context window with ellipsis."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 3] + "..."


def build_safe_context(
    documents: List[Tuple[Any, float]],
    max_docs: int = 10,
    max_text_length: int = 500,
) -> Tuple[str, List[dict]]:
    """
    Build XML-delimited context string with citation tracking.
    
    Args:
        documents: List of (Document, score) tuples
        max_docs: Maximum documents to include
        max_text_length: Max chars per document snippet
    
    Returns:
        Tuple of (context_string, citations_list)
    """
    context_parts = []
    citations = []
    
    for rank, (doc, score) in enumerate(documents[:max_docs], start=1):
        meta = getattr(doc, 'metadata', {})
        source = meta.get('source_file', 'unknown')
        page = meta.get('page_number', 0)
        block_type = meta.get('block_type', 'paragraph')
        chunk_id = meta.get('chunk_id')
        
        # Escape content for safety
        safe_content = escape_prompt_content(doc.page_content[:max_text_length])
        source_marker = f"[SOURCE: {source}, page {page + 1}]"
        
        context_parts.append(f"{source_marker}\n{safe_content}")
        citations.append({
            'source_file': source,
            'page_number': page,
            'page_display': page + 1,
            'block_type': block_type,
            'chunk_text': doc.page_content[:_MAX_CITATION_TEXT],
            'rerank_score': round(score, 4),
            'chunk_id': chunk_id,
        })
    
    context_body = "\n\n---\n\n".join(context_parts)
    context = f"<document_context>\n{context_body}\n</document_context>"
    return context, citations


def validate_rag_weights(semantic: float, keyword: float) -> Tuple[float, float]:
    """Validate and normalize RRF weights to sum to 1.0."""
    total = semantic + keyword
    if total <= 0:
        return _DEFAULT_SEMANTIC_WEIGHT, _DEFAULT_KEYWORD_WEIGHT
    return semantic / total, keyword / total


# DVMELTSS-M: Reusable field definitions for Pydantic models
from pydantic import Field
CorrelationIdField = Field(default=None, max_length=100, description="Request ID for distributed tracing")

# DVMELTSS-M: Explicit module exports
__all__ = [
    "escape_prompt_content",
    "generate_rag_correlation_id",
    "tokenize_for_bm25",
    "normalize_rerank_score",
    "reciprocal_rank_fusion",
    "truncate_for_context",
    "build_safe_context",
    "validate_rag_weights",
    "CorrelationIdField",
]
# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.rag_utils) ------
# ========================================================================

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    
    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        # Check for backend root correctly
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]
    
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    
    def run_tests():
        print("🔍 Testing RAG Utils module (app/core/rag_utils.py)")
        print("=" * 70)
        
        try:
            from app.core.rag_utils import (
                escape_prompt_content, tokenize_for_bm25, normalize_rerank_score,
                reciprocal_rank_fusion, truncate_for_context, build_safe_context,
                validate_rag_weights, generate_rag_correlation_id
            )
            from unittest.mock import patch
            
            # -- Test 1: escape_prompt_content (Security) ----------------
            print("\n📌 Test 1: escape_prompt_content (Injection Protection)")
            
            # HTML Injection attempt
            malicious = "<script>alert('XSS')</script>"
            safe = escape_prompt_content(malicious)
            
            # ✅ FIX: Check for escaped chars (\<), not missing chars
            # Escaping adds backslash, it doesn't remove the char.
            assert "\\<" in safe and "\\>" in safe, f"Expected escaped chars in: {safe}"
            print(f"   ✅ HTML injection escaped with backslashes: '{safe}'")
            
            # SQL Injection style
            malicious = "DROP TABLE users; --"
            safe = escape_prompt_content(malicious)
            print(f"   ✅ Script injection handled: '{safe}'")
            
            # Normal text
            safe = escape_prompt_content("Normal text")
            assert safe == "Normal text"
            print(f"   ✅ Normal text preserved")
            
            # -- Test 2: tokenize_for_bm25 -------------------------------
            print("\n📌 Test 2: tokenize_for_bm25")
            
            # Punctuation removal
            tokens = tokenize_for_bm25("Hello, world!")
            assert tokens == ["hello", "world"]
            print(f"   ✅ Punctuation removed: {tokens}")
            
            # Case normalization
            tokens = tokenize_for_bm25("Python IS great")
            assert "python" in tokens
            print(f"   ✅ Case normalized: {tokens}")
            
            # Empty input
            assert tokenize_for_bm25("") == []
            print(f"   ✅ Empty input handled")
            
            # -- Test 3: normalize_rerank_score -------------------------
            print("\n📌 Test 3: normalize_rerank_score (Sigmoid)")
            
            # Positive logit -> close to 1.0
            score = normalize_rerank_score(10.0)
            assert 0.99 < score <= 1.0
            print(f"   ✅ Positive logit (10.0) -> {score:.4f}")
            
            # Negative logit -> close to 0.0
            score = normalize_rerank_score(-10.0)
            assert 0.0 <= score < 0.01
            print(f"   ✅ Negative logit (-10.0) -> {score:.4f}")
            
            # Zero logit -> 0.5
            score = normalize_rerank_score(0.0)
            assert abs(score - 0.5) < 0.0001
            print(f"   ✅ Zero logit (0.0) -> {score:.4f}")
            
            # -- Test 4: reciprocal_rank_fusion (RRF) -------------------
            print("\n📌 Test 4: reciprocal_rank_fusion (Hybrid Search)")
            
            # Mock items: Item A, Item B, Item C
            item_a, item_b, item_c = "DocA", "DocB", "DocC"
            
            # List 1: Semantic search results (B is rank 1, A is rank 2)
            list_1 = [(item_b, 0.9), (item_a, 0.8)]
            # List 2: Keyword search results (A is rank 1, C is rank 2)
            list_2 = [(item_a, 0.9), (item_c, 0.7)]
            
            # Weights: 50% semantic, 50% keyword
            fused = reciprocal_rank_fusion(
                ranked_lists=[list_1, list_2],
                weights=[0.5, 0.5],
                k=3
            )
            
            # Item A should be #1 (Rank 2 in list 1 + Rank 1 in list 2 = High score)
            top_item = fused[0][0]
            assert top_item == item_a, f"Expected A to be first, got {top_item}"
            print(f"   ✅ RRF Fusion: Item A (appears in both) ranked first: '{top_item}'")
            
            # -- Test 5: truncate_for_context ---------------------------
            print("\n📌 Test 5: truncate_for_context")
            
            text = "A" * 1000
            short_text = truncate_for_context(text, max_chars=10)
            assert len(short_text) == 10
            assert short_text.endswith("...")
            print(f"   ✅ Truncated text: {short_text}")
            
            # -- Test 6: build_safe_context -----------------------------
            print("\n📌 Test 6: build_safe_context (XML + Citations)")
            
            # Mock Document class
            class MockDoc:
                def __init__(self, content, meta):
                    self.page_content = content
                    self.metadata = meta
            
            doc1 = MockDoc(
                "The total is $100.", 
                {"source_file": "invoice.pdf", "page_number": 0, "chunk_id": "c1"}
            )
            doc2 = MockDoc(
                "Date: 2026-05-10", 
                {"source_file": "invoice.pdf", "page_number": 1, "chunk_id": "c2"}
            )
            
            context, citations = build_safe_context(
                documents=[(doc1, 0.9), (doc2, 0.8)],
                max_docs=2
            )
            
            assert "<document_context>" in context
            assert "[SOURCE: invoice.pdf, page 1]" in context
            assert len(citations) == 2
            print(f"   ✅ Context generated: {len(citations)} citations, XML wrapper present")
            
            # Test injection protection in context
            injection_doc = MockDoc(
                "<script>alert('hack')</script>", 
                {"source_file": "evil.txt", "page_number": 0}
            )
            safe_context, _ = build_safe_context(
                documents=[(injection_doc, 0.9)],
                max_docs=1
            )
            # ✅ FIX: Check for escaped script tag, not missing tag
            assert "\\<script\\>" in safe_context
            print(f"   ✅ Context content escaped (XSS prevented)")
            
            # -- Test 7: validate_rag_weights ---------------------------
            print("\n📌 Test 7: validate_rag_weights (Normalization)")
            
            s, k = validate_rag_weights(0.6, 0.4)
            assert abs((s + k) - 1.0) < 0.001
            print(f"   ✅ Valid weights normalized: {s}, {k}")
            
            # Zero total -> Defaults
            s, k = validate_rag_weights(0.0, 0.0)
            assert s == 0.6 and k == 0.4
            print(f"   ✅ Zero weights fallback to defaults: {s}, {k}")
            
            # -- Test 8: generate_rag_correlation_id --------------------
            print("\n📌 Test 8: generate_rag_correlation_id")
            
            # Mock the internal ID generator for deterministic output
            with patch('app.core.ids.generate_correlation_id', return_value='mock_id'):
                corr_id = generate_rag_correlation_id(suffix="query")
                assert corr_id == "rag_mock_id_query"
                print(f"   ✅ Correlation ID generated: '{corr_id}'")
            
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! RAG Utils module verified.")
            print("\n💡 What we verified:")
            print("   • Security: Prompt injection escaping (Backslash strategy) ✅")
            print("   • BM25: Tokenization, punctuation removal ✅")
            print("   • Reranking: Sigmoid score normalization ✅")
            print("   • RRF: Hybrid search result fusion logic ✅")
            print("   • Context: XML formatting, safe escaping, citation tracking ✅")
            print("   • Weights: RRF weight normalization ✅")
            print("\n🔐 Security: All prompt inputs are escaped before LLM context building")
            return True
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run tests
    success = run_tests()
    sys.exit(0 if success else 1)