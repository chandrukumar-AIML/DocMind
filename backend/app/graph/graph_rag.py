
from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from typing import Final, Optional, Any

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.graph_utils import generate_graph_correlation_id
from .cypher_retriever import CypherRetriever
from app.vectorstore.store_manager import VectorStoreManager

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-M) -------------------------
# ========================================================================

# Keywords that suggest graph traversal is needed
_GRAPH_TRIGGER_KEYWORDS: Final = frozenset(
    {
        "who",
        "relationship",
        "connected",
        "between",
        "involves",
        "signed",
        "related",
        "link",
        "path",
        "through",
        "via",
        "all contracts",
        "all documents",
        "which company",
        "what entities",
        "party",
        "parties",
        "counterparty",
        "signatory",
        "affiliates",
    }
)

# DVMELTSS-V: Retrieval limits
_MAX_VECTOR_RESULTS: Final = 10
_MAX_GRAPH_RESULTS: Final = 20
_MAX_CONTEXT_CHARS: Final = 4000

# BATMAN-T: Timeout for individual retrieval calls
_RETRIEVAL_TIMEOUT_SECONDS: Final = 30.0


@dataclass(frozen=True)
class GraphRAGResult:
    """
    Immutable combined result from graph + vector retrieval.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    answer_context: str  # merged context for LLM
    vector_docs: list[Document]  # from ChromaDB
    graph_records: list[dict]  # from Neo4j
    graph_context: str  # formatted graph text
    retrieval_mode: str  # "vector" | "graph" | "hybrid"
    vector_latency_ms: float = 0.0
    graph_latency_ms: float = 0.0
    entity_count: int = 0
    relationship_count: int = 0
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for API responses / logging."""
        return {
            "answer_context": self.answer_context[:500] + ("..." if len(self.answer_context) > 500 else ""),
            "vector_doc_count": len(self.vector_docs),
            "graph_record_count": len(self.graph_records),
            "retrieval_mode": self.retrieval_mode,
            "vector_latency_ms": round(self.vector_latency_ms, 2),
            "graph_latency_ms": round(self.graph_latency_ms, 2),
            "entity_count": self.entity_count,
            "relationship_count": self.relationship_count,
            "correlation_id": self.correlation_id,
        }


class GraphRAGRetriever:
    """
    Hybrid retriever combining Neo4j graph search + ChromaDB vector search.

    Features (DVMELTSS-V, BATMAN-A):
    - Auto-detect retrieval mode from query keywords
    - Async concurrent retrieval for lower latency
    - Safe Cypher generation with injection prevention
    - Context merging with character limits to prevent overflow
    - Correlation ID tracing for distributed debugging
    """

    def __init__(
        self,
        store_manager: Optional[VectorStoreManager] = None,
        cypher_retriever: Optional[CypherRetriever] = None,
    ):
        self.store = store_manager or VectorStoreManager()
        self.cypher = cypher_retriever or CypherRetriever()
        logger.info("GraphRAGRetriever initialized with hybrid retrieval")

    def _validate_inputs(
        self,
        query: str,
        workspace_id: str,
        filter_dict: Optional[dict],
        corr_id: str,
    ) -> tuple[bool, str]:
        """Validate inputs before processing."""
        if not isinstance(query, str) or not query.strip():
            return False, "query must be a non-empty string"
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return False, "workspace_id must be a non-empty string"
        if filter_dict is not None and not isinstance(filter_dict, dict):
            return False, "filter_dict must be a dict or None"
        return True, ""

    def _detect_mode(self, query: str) -> str:
        """
        DVMELTSS-V: Auto-detect retrieval mode from query content.
        Graph queries typically ask about relationships, entities, paths.
        """
        query_lower = query.lower()
        graph_score = sum(1 for kw in _GRAPH_TRIGGER_KEYWORDS if kw in query_lower)

        if graph_score >= 2:
            return "hybrid"
        elif graph_score == 1:
            return "hybrid"
        else:
            return "vector"

    async def _retrieve_vector_async(
        self,
        query: str,
        workspace_id: str,
        k: int,
        filter_dict: Optional[dict],
        correlation_id: str,
    ) -> tuple[list[Document], float]:
        """Async vector retrieval with timeout guard."""
        # Resolve a workspace-scoped store instead of self.store (fixed at construction,
        # shared across all workspaces) — otherwise every graph query would search
        # whichever workspace's collection self.store happened to be built against.
        from app.dependencies import get_store_manager_for_workspace

        store = get_store_manager_for_workspace(workspace_id) if workspace_id else self.store
        start = time.perf_counter()
        try:
            # ✅ Use asyncio.to_thread for Python 3.9+ or fallback for 3.8
            if sys.version_info >= (3, 9):
                results = await asyncio.wait_for(
                    asyncio.to_thread(lambda: store.search(query=query, k=k, filter_dict=filter_dict)),
                    timeout=_RETRIEVAL_TIMEOUT_SECONDS,
                )
            else:
                loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                results = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: store.search(query=query, k=k, filter_dict=filter_dict),
                    ),
                    timeout=_RETRIEVAL_TIMEOUT_SECONDS,
                )
            latency = (time.perf_counter() - start) * 1000
            docs = [doc for doc, _ in results]
            logger.debug(f"[{correlation_id}] Vector retrieval: {len(docs)} docs in {latency:.0f}ms")
            return docs, latency
        except asyncio.TimeoutError:
            logger.warning(f"[{correlation_id}] Vector retrieval timed out after {_RETRIEVAL_TIMEOUT_SECONDS}s")
            return [], 0.0
        except Exception as e:
            logger.error(f"[{correlation_id}] Vector retrieval failed: {e}")
            return [], 0.0

    async def _retrieve_graph_async(
        self,
        query: str,
        workspace_id: str,
        correlation_id: str,
    ) -> tuple[str, list[dict], float]:
        """Async graph retrieval with timeout guard."""
        start = time.perf_counter()
        try:
            if sys.version_info >= (3, 9):
                graph_context, graph_records = await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: self.cypher.retrieve(
                            query=query,
                            workspace_id=workspace_id,
                            use_text_to_cypher=True,
                            correlation_id=correlation_id,
                        )
                    ),
                    timeout=_RETRIEVAL_TIMEOUT_SECONDS,
                )
            else:
                loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
                graph_context, graph_records = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        lambda: self.cypher.retrieve(
                            query=query,
                            workspace_id=workspace_id,
                            use_text_to_cypher=True,
                            correlation_id=correlation_id,
                        ),
                    ),
                    timeout=_RETRIEVAL_TIMEOUT_SECONDS,
                )
            latency = (time.perf_counter() - start) * 1000
            logger.debug(f"[{correlation_id}] Graph retrieval: {len(graph_records)} records in {latency:.0f}ms")
            return graph_context, graph_records, latency
        except asyncio.TimeoutError:
            logger.warning(f"[{correlation_id}] Graph retrieval timed out after {_RETRIEVAL_TIMEOUT_SECONDS}s")
            return "", [], 0.0
        except Exception as e:
            logger.error(f"[{correlation_id}] Graph retrieval failed: {e}")
            return "", [], 0.0

    def _merge_context(
        self,
        vector_docs: list[Document],
        graph_context: str,
        mode: str,
    ) -> str:
        """
        Combine vector and graph context into single LLM input.
        DVMELTSS-S: Truncate to prevent context window overflow.
        ✅ FIXED: Handle empty inputs gracefully.
        """
        parts = []

        if graph_context and graph_context.strip() and mode in ("graph", "hybrid"):
            # Truncate graph context if too long
            safe_graph = graph_context[: _MAX_CONTEXT_CHARS // 2]
            parts.append(f"=== Knowledge Graph Context ===\n{safe_graph}")

        if vector_docs and mode in ("vector", "hybrid"):
            vector_parts = []
            for doc in vector_docs[:5]:  # top 5 vector chunks
                meta = doc.metadata
                source = meta.get("source_file", "unknown")
                page = meta.get("page_number", 0) + 1
                # Truncate each chunk
                content = doc.page_content[:500] + ("..." if len(doc.page_content) > 500 else "")
                vector_parts.append(f"[SOURCE: {source}, page {page}]\n{content}")

            if vector_parts:
                safe_vector = "\n\n---\n\n".join(vector_parts)
                if len(safe_vector) > _MAX_CONTEXT_CHARS // 2:
                    safe_vector = safe_vector[: _MAX_CONTEXT_CHARS // 2] + "\n\n[...truncated...]"
                parts.append(f"=== Vector Search Context ===\n{safe_vector}")

        if not parts:
            logger.debug("No context to merge — returning empty")
            return ""

        merged = "\n\n===\n\n".join(parts)
        # Final safety truncate
        return merged[:_MAX_CONTEXT_CHARS] + ("..." if len(merged) > _MAX_CONTEXT_CHARS else "")

    async def retrieve_async(
        self,
        query: str,
        workspace_id: str = "default",
        mode: str = "auto",
        k_vector: int = _MAX_VECTOR_RESULTS,
        filter_dict: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> GraphRAGResult:
        """
        Async version: Retrieve context using graph + vector hybrid approach.
        BATMAN-A: Concurrent retrieval for lower latency.
        ✅ FIXED: Input validation + safe empty handling.
        """
        corr_id = correlation_id or generate_graph_correlation_id("graphrag")

        # ✅ Validate inputs
        is_valid, error = self._validate_inputs(query, workspace_id, filter_dict, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid inputs: {error}")
            return GraphRAGResult(
                answer_context="",
                vector_docs=[],
                graph_records=[],
                graph_context="",
                retrieval_mode=mode,
                correlation_id=corr_id,
            )

        if mode == "auto":
            mode = self._detect_mode(query)
        logger.info(f"[{corr_id}] GraphRAG mode: {mode} | query: '{query[:60]}...'")

        # Run retrievals concurrently
        vector_task = self._retrieve_vector_async(query, workspace_id, k_vector, filter_dict, corr_id)
        graph_task = self._retrieve_graph_async(query, workspace_id, corr_id)

        vector_docs, v_latency = await vector_task
        graph_context, graph_records, g_latency = await graph_task

        # Merge context
        answer_context = self._merge_context(vector_docs, graph_context, mode)

        # Count entities/relationships in graph records
        entity_count = len(graph_records)
        rel_count = sum(len(r.get("connections", [])) for r in graph_records if isinstance(r.get("connections"), list))

        return GraphRAGResult(
            answer_context=answer_context,
            vector_docs=vector_docs,
            graph_records=graph_records,
            graph_context=graph_context,
            retrieval_mode=mode,
            vector_latency_ms=v_latency,
            graph_latency_ms=g_latency,
            entity_count=entity_count,
            relationship_count=rel_count,
            correlation_id=corr_id,
        )

    def retrieve(
        self,
        query: str,
        workspace_id: str = "default",
        mode: str = "auto",
        k_vector: int = _MAX_VECTOR_RESULTS,
        filter_dict: Optional[dict] = None,
        correlation_id: Optional[str] = None,
    ) -> GraphRAGResult:
        """
        Sync wrapper — use retrieve_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return empty
            logger.warning(
                "⚠️ GraphRAGRetriever.retrieve() called from async context — "
                "use retrieve_async() instead. Returning empty result."
            )
            return GraphRAGResult(
                answer_context="",
                vector_docs=[],
                graph_records=[],
                graph_context="",
                retrieval_mode=mode,
                correlation_id=correlation_id or "sync_wrapper",
            )
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(self.retrieve_async(query, workspace_id, mode, k_vector, filter_dict, correlation_id))


def get_graphrag_metadata() -> dict[str, Any]:
    """✅ NEW: Return GraphRAG metadata for monitoring."""
    return {
        "trigger_keywords": list(_GRAPH_TRIGGER_KEYWORDS),
        "max_vector_results": _MAX_VECTOR_RESULTS,
        "max_graph_results": _MAX_GRAPH_RESULTS,
        "max_context_chars": _MAX_CONTEXT_CHARS,
        "retrieval_timeout": _RETRIEVAL_TIMEOUT_SECONDS,
    }


# DVMELTSS-M: Explicit module exports
__all__ = ["GraphRAGRetriever", "GraphRAGResult", "get_graphrag_metadata"]
# Local smoke test entry point. Run: python -m

