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

import logging
import re
import math
from typing import Final, List, Tuple, Any


logger = logging.getLogger(__name__)

# DVMELTSS-S: Safe patterns for text processing
_PROMPT_ESCAPE_PATTERN: Final = re.compile(r'[<>&"\'\\]')
_TOKENIZE_PATTERN: Final = re.compile(r"[^\w\s]")
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
            item_id = getattr(item, "metadata", {}).get("chunk_id") or f"hash_{hash(str(item))}"
            rrf_score = weight / (rrf_k + rank)
            scores[item_id] = scores.get(item_id, 0.0) + rrf_score
            item_map[item_id] = item

    sorted_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    return [(item_map[doc_id], scores[doc_id]) for doc_id in sorted_ids[:k]]


def truncate_for_context(text: str, max_chars: int = 500) -> str:
    """Truncate text for context window with ellipsis."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


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
        meta = getattr(doc, "metadata", {})
        source = meta.get("source_file", "unknown")
        page = meta.get("page_number", 0)
        block_type = meta.get("block_type", "paragraph")
        chunk_id = meta.get("chunk_id")

        # Escape content for safety
        safe_content = escape_prompt_content(doc.page_content[:max_text_length])
        source_marker = f"[SOURCE: {source}, page {page + 1}]"

        context_parts.append(f"{source_marker}\n{safe_content}")
        citations.append(
            {
                "source_file": source,
                "page_number": page,
                "page_display": page + 1,
                "block_type": block_type,
                "chunk_text": doc.page_content[:_MAX_CITATION_TEXT],
                "rerank_score": round(score, 4),
                "chunk_id": chunk_id,
            }
        )

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

