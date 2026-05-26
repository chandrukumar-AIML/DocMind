# backend/app/crag/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling

"""
DocuMind AI - CRAG & Self-RAG Module

Implements Corrective RAG (CRAG) and Self-RAG reflection pipelines for:
- Document relevance grading with structured routing decisions
- Query decomposition for complex/ambiguous questions
- Web search fallback when internal retrieval fails
- Self-assessment of generated answers to trigger corrective retrieval

Public API:
    from app.crag import DocumentGrader, QueryDecomposer, WebSearcher, SelfRAGReflector

Usage:
    grader = DocumentGrader()
    result = await grader.grade_documents(query, docs)
    
    decomposer = QueryDecomposer()
    split = await decomposer.decompose(result.missing_info)
"""
from __future__ import annotations

# DVMELTSS-M: Explicit public API surface — prevents accidental internal imports
__all__ = [
    # Document Grader
    "DocumentGrader", "GradingResult", "DocumentGrade", "GradeLabel",
    # Query Decomposer
    "QueryDecomposer", "DecomposedQuery",
    # Web Search
    "WebSearcher", "WebSearchResult",
    # Self-RAG
    "SelfRAGReflector", "SelfRAGAssessment", "CRAGDecision",
    # Test hooks
    "_reset_crag_instances_for_tests",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "1.3.1"  # FIXED: Bumped for retry logic + async fixes
__description__ = "Corrective RAG + Self-RAG reflection pipeline"
__routing_strategy__ = "grade -> decompose/rewrite -> web_search -> self_rag -> generate"


# ========================================================================
# -- LAZY IMPORTS (DVMELTSS-T: Prevent circular deps at startup) --------
# ========================================================================

# Module-level instances — initialized on first access (for singleton pattern if needed)
_grader_instance = None
_decomposer_instance = None
_searcher_instance = None
_reflector_instance = None


def __getattr__(name: str):
    """
    Dynamically resolve imports only when accessed.
    Prevents circular imports between crag ↔ agent ↔ vectorstore ↔ ocr modules.
    Enables pytest to collect tests without initializing LLM/Redis/DB clients.
    PEP 562 compliant.
    """
    # Document Grader
    if name in ("DocumentGrader", "GradingResult", "DocumentGrade", "GradeLabel"):
        from .document_grader import DocumentGrader, GradingResult, DocumentGrade, GradeLabel
        return locals()[name]
    
    # Query Decomposer
    if name in ("QueryDecomposer", "DecomposedQuery"):
        from .query_decomposer import QueryDecomposer, DecomposedQuery
        return locals()[name]
    
    # Web Search
    if name in ("WebSearcher", "WebSearchResult"):
        from .web_search import WebSearcher, WebSearchResult
        return locals()[name]
    
    # Self-RAG
    if name in ("SelfRAGReflector", "SelfRAGAssessment", "CRAGDecision"):
        from .self_rag import SelfRAGReflector, SelfRAGAssessment, CRAGDecision
        return locals()[name]
    
    # Test hooks
    if name == "_reset_crag_instances_for_tests":
        return _reset_crag_instances_for_tests
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# DVMELTSS-T: Test hook — reset instances for isolated test runs
def _reset_crag_instances_for_tests() -> None:
    """
    Reset module-level instances — for pytest fixtures only.
    
    Usage in conftest.py:
        @pytest.fixture(autouse=True)
        def reset_crag():
            from app.crag import _reset_crag_instances_for_tests
            _reset_crag_instances_for_tests()
            yield
    """
    global _grader_instance, _decomposer_instance, _searcher_instance, _reflector_instance
    _grader_instance = None
    _decomposer_instance = None
    _searcher_instance = None
    _reflector_instance = None


# DVMELTSS-L: Module initialization logging for observability
def _log_module_init() -> None:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"CRAG module loaded | version={__version__} | strategy={__routing_strategy__}")

# Auto-log on import (safe — only runs once per process)
_log_module_init()
