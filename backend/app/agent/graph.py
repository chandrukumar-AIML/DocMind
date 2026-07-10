
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable, Any, TYPE_CHECKING, Optional, cast

from langgraph.graph import StateGraph

try:
    from langgraph.graph import END
except ImportError:
    END = "__end__"  # type: ignore

try:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.checkpoint.base import BaseCheckpointSaver
except ImportError:
    MemorySaver = None
    BaseCheckpointSaver = None  # type: ignore[assignment,misc]


def _build_checkpointer() -> Optional["BaseCheckpointSaver"]:
    """
    Return the best available LangGraph checkpointer:

    1. PostgresSaver  — persistent, multi-worker safe (requires psycopg2 + DB URL)
    2. RedisSaver     — persistent, multi-worker safe (requires redis + REDIS_URL)
    3. MemorySaver    — in-process only, NOT safe for multi-worker deployments
    4. None           — no checkpointing

    Preference order ensures horizontal scalability when infrastructure is available.
    """
    from app.config import get_settings
    settings = get_settings()

    # Try PostgresSaver first (persistent, shared across workers)
    db_url = getattr(settings, "database_url", None)
    if db_url and "postgresql" in db_url:
        try:
            # langgraph-checkpoint-postgres (pip install langgraph-checkpoint-postgres)
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore
            sync_url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")
            checkpointer = PostgresSaver.from_conn_string(sync_url)
            logger.info("Using PostgresSaver checkpointer (persistent, multi-worker safe)")
            return checkpointer
        except (ImportError, Exception) as e:
            logger.info(f"PostgresSaver unavailable ({e}), trying RedisSaver")

    # Try RedisSaver (persistent, shared across workers)
    redis_url = getattr(settings, "redis_url", None)
    if redis_url:
        try:
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore
            checkpointer = RedisSaver.from_conn_string(redis_url)
            logger.info("Using RedisSaver checkpointer (persistent, multi-worker safe)")
            return checkpointer
        except (ImportError, Exception) as e:
            logger.info(f"RedisSaver unavailable ({e}), falling back to MemorySaver")

    # MemorySaver fallback — in-process only, state lost on restart/between workers
    if MemorySaver:
        logger.warning(
            "Using MemorySaver checkpointer — state is IN-PROCESS ONLY. "
            "Install langgraph-checkpoint-postgres or langgraph-checkpoint-redis "
            "for multi-worker persistent agent state."
        )
        return MemorySaver()

    logger.warning("No checkpointer available — agent state will not persist")
    return None

from .state import AgentState
from .nodes import (
    node_query_analyzer,
    node_vector_retriever,
    node_graph_retriever,
    node_relevance_grader,
    node_crag_grader,
    node_query_rewriter,
    node_web_search,
    node_query_decomposer,
    node_answer_generator,
    node_self_rag_reflector,
    node_hallucination_checker,
    node_human_review,
)
from .edges import (
    route_after_analysis,
    route_after_grading,
    route_after_retry,
    route_after_hallucination_check,
    route_after_crag_grading,
    route_after_self_rag,
    route_after_web_search,
    route_after_decomposer,
)

logger = logging.getLogger(__name__)

_AGENT_GRAPH_VERSION: str = "phase_e_crag_selfrag_v2"

if TYPE_CHECKING:
    RouterFunc = Callable[[AgentState], str]
else:
    # Runtime: use cast to satisfy type checker without triggering Pylance
    RouterFunc = cast(type, Callable[[AgentState], str])

#           Pylance reportInvalidTypeForm when END is used as a dict key.
_END_KEY: str = "__end__"


def _validate_node_async(node_func: Callable[..., Any], node_name: str) -> bool:
    """Validate that node function is async for LangGraph compatibility."""
    import asyncio

    if not asyncio.iscoroutinefunction(node_func):
        logger.warning(f"⚠️ Node '{node_name}' is not async — LangGraph may block event loop")
        return False
    return True


def _add_conditional_edge_safe(
    graph: StateGraph,
    source: str,
    router: RouterFunc,
    mapping: dict[str, str],
    description: str = "",
):
    """Helper to add conditional edges with logging + validation."""
    if description:
        logger.debug(f"Adding conditional edge: {source} -> {description}")

    if not callable(router):
        raise TypeError(f"Router for '{source}' must be callable, got {type(router)}")

    try:
        graph.add_conditional_edges(source, router, mapping)
    except Exception as e:
        logger.error(f"Failed to add conditional edge {source}: {e}")
        raise


def _wrap_router_with_validation(router: RouterFunc, expected_targets: set[str]) -> RouterFunc:
    """Wrap router to validate return value against allowed targets."""

    def validated_router(state: AgentState) -> str:
        result = router(state)
        valid_targets = expected_targets | {_END_KEY, str(END)}
        if str(result) not in valid_targets:
            # a registered destination for one of the seven conditional edges that use this
            # wrapper — for every other edge it isn't in the edge's own `mapping` dict, so
            # LangGraph raised an uncaught KeyError instead of degrading gracefully. Fall
            # back to any target this specific edge actually registers instead.
            fallback = next(iter(expected_targets), _END_KEY)
            logger.error(
                f"Router returned invalid target '{result}' — expected one of {valid_targets}. "
                f"Defaulting to '{fallback}'."
            )
            return fallback
        return result

    return validated_router  # type: ignore


def _get_langsmith_callbacks() -> list:
    """
    FIXED: Return LangSmith callback handler if configured.
    Allows every graph node invocation to emit a LangSmith trace span.
    """
    try:
        from app.observability.langsmith_tracer import get_langsmith_callback

        cb = get_langsmith_callback()
        if cb:
            return [cb]
    except Exception as e:
        logger.debug(f"LangSmith callbacks not available: {e}")
    return []


def build_agent_graph(version: str = _AGENT_GRAPH_VERSION) -> Any:
    """Builds the LangGraph agent workflow with CRAG + Self-RAG pipeline (Phase E)."""
    logger.info(f"Building agent graph version: {version}")

    graph = StateGraph(AgentState)

    nodes = {
        "query_analyzer": node_query_analyzer,
        "vector_retriever": node_vector_retriever,
        "graph_retriever": node_graph_retriever,
        "relevance_grader": node_relevance_grader,
        "crag_grader": node_crag_grader,
        "query_rewriter": node_query_rewriter,
        "web_search": node_web_search,
        "query_decomposer": node_query_decomposer,
        "answer_generator": node_answer_generator,
        "self_rag_reflector": node_self_rag_reflector,
        "hallucination_checker": node_hallucination_checker,
        "human_review": node_human_review,
    }

    for name, func in nodes.items():
        try:
            _validate_node_async(func, name)
            graph.add_node(name, func)
        except Exception as e:
            logger.error(f"Failed to register node '{name}': {e}")
            raise

    graph.set_entry_point("query_analyzer")

    _add_conditional_edge_safe(
        graph,
        "query_analyzer",
        route_after_analysis,
        {
            "vector_retriever": "vector_retriever",
            "graph_retriever": "graph_retriever",
            "hybrid_retrieve_start": "vector_retriever",
        },
        description="Analysis -> Retrieval route selection",
    )

    _add_conditional_edge_safe(
        graph,
        "vector_retriever",
        _wrap_router_with_validation(route_after_grading, {"graph_retriever", "crag_grader"}),
        {
            "graph_retriever": "graph_retriever",
            "crag_grader": "crag_grader",
        },
        description="Vector retrieval -> Hybrid/CRAG routing",
    )

    graph.add_edge("graph_retriever", "crag_grader")

    _add_conditional_edge_safe(
        graph,
        "crag_grader",
        _wrap_router_with_validation(
            route_after_crag_grading,
            {"answer_generator", "web_search", "query_rewriter", "query_decomposer"},
        ),
        {
            "answer_generator": "answer_generator",
            "web_search": "web_search",
            "query_rewriter": "query_rewriter",
            "query_decomposer": "query_decomposer",
        },
        description="CRAG grading -> Generate/Web/Rewrite/Decompose",
    )

    _add_conditional_edge_safe(
        graph,
        "web_search",
        _wrap_router_with_validation(route_after_web_search, {"answer_generator"}),
        {"answer_generator": "answer_generator"},
        description="Web search fallback -> Generation",
    )

    _add_conditional_edge_safe(
        graph,
        "query_decomposer",
        _wrap_router_with_validation(route_after_decomposer, {"vector_retriever"}),
        {"vector_retriever": "vector_retriever"},
        description="Decomposed query -> Retry retrieval",
    )

    _add_conditional_edge_safe(
        graph,
        "query_rewriter",
        _wrap_router_with_validation(route_after_retry, {"vector_retriever", "graph_retriever"}),
        {
            "vector_retriever": "vector_retriever",
            "graph_retriever": "graph_retriever",
        },
        description="Rewritten query -> Retry original route",
    )

    graph.add_edge("answer_generator", "self_rag_reflector")

    _add_conditional_edge_safe(
        graph,
        "self_rag_reflector",
        _wrap_router_with_validation(route_after_self_rag, {"vector_retriever", "hallucination_checker"}),
        {
            "vector_retriever": "vector_retriever",
            "hallucination_checker": "hallucination_checker",
        },
        description="Self-RAG reflection -> Retrieve more or validate",
    )

    #           END as a key triggers Pylance reportInvalidTypeForm because
    #           Pylance interprets dict keys in conditional edge mappings as
    #           type expressions. Using "__end__" (via _END_KEY) is equivalent
    #           at runtime since END resolves to "__end__" in all LangGraph versions.
    _add_conditional_edge_safe(
        graph,
        "hallucination_checker",
        _wrap_router_with_validation(route_after_hallucination_check, {"human_review", _END_KEY}),
        {
            "human_review": "human_review",
            _END_KEY: END,  # type: ignore[arg-type]
        },
        description="Hallucination check -> End or human review",
    )

    graph.add_edge("human_review", END)  # type: ignore[arg-type]

    checkpointer = _build_checkpointer()

    compile_kwargs: dict[str, Any] = {"checkpointer": checkpointer}
    try:
        import langgraph

        if hasattr(langgraph, "__version__"):
            major, minor = map(int, langgraph.__version__.split(".")[:2])
            if major > 0 or minor >= 2:
                compile_kwargs["interrupt_before"] = ["human_review"]
    except Exception:
        logger.debug("Could not determine LangGraph version — skipping interrupt_before")

    langsmith_cbs = _get_langsmith_callbacks()
    if langsmith_cbs:
        compile_kwargs["callbacks"] = langsmith_cbs
        logger.info(f"LangSmith tracing enabled: {len(langsmith_cbs)} callback(s)")

    try:
        compiled = graph.compile(**compile_kwargs)
    except TypeError as e:
        if "interrupt_before" in str(e):
            logger.warning("⚠️ interrupt_before not supported — compiling without it")
            compiled = graph.compile(checkpointer=checkpointer)
        else:
            raise

    logger.info(f"Agent graph compiled successfully (version={version}, nodes={len(graph.nodes)})")
    return compiled


@lru_cache(maxsize=1)
def get_agent_graph(version: str = _AGENT_GRAPH_VERSION) -> Any:
    """Singleton accessor for compiled agent graph."""
    if version != _AGENT_GRAPH_VERSION:
        logger.warning(
            f"Requested graph version '{version}' != current '{_AGENT_GRAPH_VERSION}'. "
            f"Returning current version. To rebuild, call reset_agent_graph_cache() and retry."
        )
    return build_agent_graph(_AGENT_GRAPH_VERSION)


def reset_agent_graph_cache() -> None:
    """Clear the LRU cache for testing graph changes without process restart."""
    get_agent_graph.cache_clear()
    logger.debug("Agent graph cache cleared.")


def get_graph_metadata() -> dict[str, Any]:
    """Return graph metadata for monitoring/debugging."""
    return {
        "version": _AGENT_GRAPH_VERSION,
        "node_count": 12,
        "nodes": [
            "query_analyzer",
            "vector_retriever",
            "graph_retriever",
            "relevance_grader",
            "crag_grader",
            "query_rewriter",
            "web_search",
            "query_decomposer",
            "answer_generator",
            "self_rag_reflector",
            "hallucination_checker",
            "human_review",
        ],
        "entry_point": "query_analyzer",
        "terminal": _END_KEY,
        "checkpointing": MemorySaver is not None,
    }


__all__ = [
    "build_agent_graph",
    "get_agent_graph",
    "reset_agent_graph_cache",
    "get_graph_metadata",
    "_AGENT_GRAPH_VERSION",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.agent.graph) ---------
# ========================================================================

