
"""
Routing functions for LangGraph conditional edges.

Each function returns a string that must match a key in the routing dictionary
defined in graph.py. LangGraph uses this to decide the next node.

Design: Pure functions, no side effects, explicit return types, observability logging.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.config import get_settings
from app.agent.state import AgentState

logger = logging.getLogger(__name__)
# _MAX_RETRIES now uses a lazy getter so settings are read at first call.


def _max_retries() -> int:
    """Lazily read agent_max_retries from settings (avoids import-time crash)."""
    return getattr(get_settings(), "agent_max_retries", 2)


# Backward-compat module-level name — computed lazily via property-style access
_MAX_RETRIES: int = 2  # safe default; overridden at runtime via _max_retries()


def _validate_route_target(target: str, allowed: set[str], corr_id: str, context: str) -> str:
    """
    ✅ NEW: Validate that returned route target is allowed.
    Prevents silent graph corruption from typos or logic errors.
    """
    if target not in allowed:
        logger.error(
            f"[{corr_id}] {context}: Invalid route target '{target}' — "
            f"expected one of {allowed}. Defaulting to first allowed target."
        )
        return next(iter(allowed))
    return target


def route_after_analysis(
    state: AgentState,
) -> Literal["vector_retriever", "graph_retriever", "hybrid_retrieve_start"]:
    """
    Routes to retrieval strategy based on Query Analyzer output.
    ✅ FIXED: Return values aligned with graph.py routing dict.

    Matches keys: vector_retriever, graph_retriever, hybrid_retrieve_start
    """
    corr_id = state.get("correlation_id", "route_after_analysis")
    route = state.get("retrieval_route", "vector")

    if route == "graph":
        logger.debug(f"[{corr_id}] Routing to graph_retriever (relational query)")
        return "graph_retriever"
    if route == "hybrid":
        logger.debug(f"[{corr_id}] Routing to hybrid_retrieve_start (starts with vector)")
        return "hybrid_retrieve_start"

    logger.debug(f"[{corr_id}] Routing to vector_retriever (default/factual query)")
    return "vector_retriever"


def route_after_grading(
    state: AgentState,
) -> Literal["graph_retriever", "crag_grader"]:
    """
    Routes vector retrieval output to graph retriever (for hybrid) or CRAG grader.
    ✅ FIXED: Safe .get() + correlation_id logging.

    Matches keys: graph_retriever, crag_grader
    """
    corr_id = state.get("correlation_id", "route_after_grading")

    if state.get("retrieval_route") == "hybrid":
        logger.debug(f"[{corr_id}] Hybrid route: proceeding to graph_retriever after vector")
        return "graph_retriever"
    return "crag_grader"


def route_after_retry(
    state: AgentState,
) -> Literal["vector_retriever", "graph_retriever"]:
    """
    Routes rewritten query back to original retrieval route.
    Prevents route drift during retry loops.
    ✅ FIXED: Safe .get() + validation.

    Matches keys: vector_retriever, graph_retriever
    """
    corr_id = state.get("correlation_id", "route_after_retry")
    route = state.get("retrieval_route", "vector")

    if route == "graph":
        return "graph_retriever"
    return "vector_retriever"


def route_after_hallucination_check(
    state: AgentState,
) -> Literal["human_review", "__end__"]:
    """
    Routes based on grounding confidence and human review flag.
    ✅ FIXED: Safe .get() + correlation_id logging.

    Matches keys: human_review, __end__ (LangGraph's END sentinel — the conditional-edge
    mapping in app/agent/graph.py registers "__end__", not "end"; returning "end" here
    never matched and made every "no review needed" outcome crash after falling through
    the validation wrapper).
    """
    corr_id = state.get("correlation_id", "route_after_hallucination_check")

    if state.get("needs_human_review", False):
        logger.info(f"[{corr_id}] Routing to human_review: low confidence or unsupported claims detected")
        return "human_review"
    return "__end__"


def route_after_crag_grading(
    state: AgentState,
) -> Literal["answer_generator", "web_search", "query_rewriter", "query_decomposer"]:
    """
    Routes based on CRAG grading action.
    Handles retry exhaustion fallback to web search.
    ✅ FIXED: Config-driven MAX_RETRIES + safe defaults.

    Matches keys: answer_generator, web_search, query_rewriter, query_decomposer
    """
    corr_id = state.get("correlation_id", "route_after_crag_grading")
    action = state.get("crag_action", "generate")
    retry_count = state.get("retry_count", 0)

    if action == "generate":
        return "answer_generator"
    elif action == "filter_and_supplement":
        return "web_search"
    elif action == "rewrite":
        return "query_rewriter" if retry_count < _max_retries() else "web_search"
    elif action == "decompose":
        return "query_decomposer"

    logger.warning(f"[{corr_id}] Unknown CRAG action: {action}. Falling back to answer_generator.")
    return "answer_generator"


def route_after_self_rag(
    state: AgentState,
) -> Literal["vector_retriever", "hallucination_checker"]:
    """
    Routes based on Self-RAG reflection decision.
    ✅ FIXED: Safe .get() + correlation_id logging.

    Matches keys: vector_retriever, hallucination_checker
    """
    corr_id = state.get("correlation_id", "route_after_self_rag")

    if state.get("self_rag_retrieve_more", False):
        logger.debug(f"[{corr_id}] Self-RAG: needs more context -> routing to vector_retriever")
        return "vector_retriever"
    return "hallucination_checker"


def route_after_web_search(
    state: AgentState,
) -> Literal["answer_generator"]:
    """
    Web search always leads to answer generation.
    ✅ FIXED: Explicit return type + correlation_id logging.

    Matches keys: answer_generator
    """
    corr_id = state.get("correlation_id", "route_after_web_search")
    logger.debug(f"[{corr_id}] Web search complete -> routing to answer_generator")
    return "answer_generator"


def route_after_decomposer(
    state: AgentState,
) -> Literal["vector_retriever"]:
    """
    Decomposed queries trigger vector retrieval for the first sub-question.
    ✅ FIXED: Explicit return type + correlation_id logging.

    Matches keys: vector_retriever
    """
    corr_id = state.get("correlation_id", "route_after_decomposer")
    logger.debug(f"[{corr_id}] Decomposed query -> routing to vector_retriever for sub-question")
    return "vector_retriever"


def get_routing_metadata() -> dict[str, any]:
    """✅ NEW: Return routing metadata for monitoring/debugging."""
    return {
        "functions": [
            "route_after_analysis",
            "route_after_grading",
            "route_after_retry",
            "route_after_hallucination_check",
            "route_after_crag_grading",
            "route_after_self_rag",
            "route_after_web_search",
            "route_after_decomposer",
        ],
        "max_retries": _MAX_RETRIES,
        "default_routes": {
            "route_after_analysis": "vector_retriever",
            "route_after_grading": "crag_grader",
            "route_after_crag_grading": "answer_generator",
        },
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "route_after_analysis",
    "route_after_grading",
    "route_after_retry",
    "route_after_hallucination_check",
    "route_after_crag_grading",
    "route_after_self_rag",
    "route_after_web_search",
    "route_after_decomposer",
    "get_routing_metadata",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.agent.edges) ---------
# ========================================================================

