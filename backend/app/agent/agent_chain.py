
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import BaseMessage

# DVMELTSS-M: Import shared LLM pool instead of creating duplicates
from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash

from .graph import get_agent_graph
from .state import AgentState

logger = logging.getLogger(__name__)

# DVMELTSS-M: Use settings instead of hardcoded constants
_MIN_QUESTION_LENGTH = getattr(settings, "agent_min_question_length", 3)
_MAX_QUESTION_LENGTH = getattr(settings, "agent_max_question_length", 2000)
_MAX_WORKSPACE_ID_LENGTH = getattr(settings, "agent_max_workspace_id_length", 64)
_DEFAULT_TIMEOUT_SECONDS = getattr(settings, "agent_default_timeout_seconds", 120)


class AgentRAGChain:
    """
    LangGraph-powered RAG agent.
    Drop-in replacement for AdvancedRAGChain — same public API.

    Key differences from v1 chain:
    - Stateful: conversation memory per thread_id
    - Self-correcting: retries retrieval up to 2 times
    - Routed: auto-selects vector/graph/hybrid per query
    - Auditable: agent_steps records every decision
    """

    def __init__(self):
        # DVMELTSS-M: Use shared graph instance
        self.graph = get_agent_graph()
        logger.info("AgentRAGChain initialized with LangGraph.")

    def _validate_thread_id(self, thread_id: str) -> bool:
        """Validate thread_id format to prevent checkpoint key collisions."""
        # Allow UUID or simple alphanumeric (max 64 chars)
        return bool(re.match(r"^[a-zA-Z0-9\-_]{1,64}$", thread_id))

    def _validate_inputs(self, question: str, workspace_id: str) -> None:
        """DVMELTSS-V: Fail-fast input validation with clear messages."""
        if not isinstance(question, str):
            raise TypeError("Question must be a string")
        if len(question.strip()) < _MIN_QUESTION_LENGTH:
            raise ValueError(f"Question must be at least {_MIN_QUESTION_LENGTH} characters")
        if len(question) > _MAX_QUESTION_LENGTH:
            raise ValueError(f"Question exceeds max length of {_MAX_QUESTION_LENGTH}")
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        if len(workspace_id) > _MAX_WORKSPACE_ID_LENGTH:
            raise ValueError(f"workspace_id exceeds max length of {_MAX_WORKSPACE_ID_LENGTH}")

    @staticmethod
    def _build_initial_state(
        question: str,
        chat_history: list[BaseMessage] | None,
        filter_dict: dict[str, Any] | None,
        workspace_id: str,
        thread_id: str,
    ) -> AgentState:
        """DVMELTSS-M: DRY state initialization for query() and stream()."""
        return {
            "correlation_id": thread_id,  # ✅ Propagate for tracing
            "question": question.strip(),
            "chat_history": chat_history or [],
            "workspace_id": workspace_id.strip(),
            "filter_dict": filter_dict or {},
            "query_type": "factual",  # ✅ Safe default instead of empty string
            "retrieval_route": "vector",
            "standalone_question": question.strip(),
            "retrieved_docs": [],
            "graph_context": "",
            "graph_records": [],
            "relevance_score": 0.0,
            "graded_docs": [],
            "retry_count": 0,
            "answer": "",
            "citations": [],
            "confidence_score": 0.0,
            "is_grounded": True,
            "hallucination_flags": [],
            "needs_human_review": False,
            "agent_steps": [],
            "error": None,
            "error_code": None,
        }

    @staticmethod
    def _build_graph_config(thread_id: str, mode: str = "query") -> dict[str, Any]:
        """DVMELTSS-M: Centralized config builder with correlated run names."""
        return {
            "configurable": {"thread_id": thread_id},
            "run_name": f"agent_{mode}_{thread_id[:8]}",
        }

    async def query(
        self,
        question: str,
        chat_history: list[BaseMessage] | None = None,
        filter_dict: dict[str, Any] | None = None,
        workspace_id: str = "default",
        thread_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Run the full agent pipeline and return complete response.
        DVMELTSS-E: Consistent success/error response structure for frontend.
        """
        thread_id = thread_id or str(uuid.uuid4())

        # ✅ Validate thread_id format
        if not self._validate_thread_id(thread_id):
            logger.warning(f"Invalid thread_id format: {thread_id} — using sanitized version")
            thread_id = re.sub(r"[^a-zA-Z0-9\-_]", "", thread_id)[:64] or str(uuid.uuid4())

        timeout = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
        logger.info(f"[{thread_id[:8]}] Starting synchronous query: '{question[:50]}...' | timeout={timeout}s")

        try:
            self._validate_inputs(question, workspace_id)
        except (TypeError, ValueError) as e:
            logger.warning(f"[{thread_id[:8]}] Input validation failed: {e}")
            return self._format_query_error(str(e), thread_id, 0.0, "VALIDATION_ERROR")

        start_ts = time.perf_counter()
        initial_state = self._build_initial_state(question, chat_history, filter_dict, workspace_id, thread_id)
        config = self._build_graph_config(thread_id, mode="query")

        try:
            final_state = await asyncio.wait_for(
                asyncio.shield(self.graph.ainvoke(initial_state, config=config)),
                timeout=timeout,
            )
            latency = time.perf_counter() - start_ts

            logger.info(f"[{thread_id[:8]}] Query completed successfully in {latency:.3f}s")
            return {
                "success": True,
                "answer": final_state.get("answer", ""),
                "citations": final_state.get("citations", []),
                "agent_steps": final_state.get("agent_steps", []),
                "retrieval_route": final_state.get("retrieval_route", "vector"),
                "query_type": final_state.get("query_type", "factual"),
                "confidence_score": final_state.get("confidence_score", 0.0),
                "relevance_score": final_state.get("relevance_score", 0.0),
                "is_grounded": final_state.get("is_grounded", True),
                "needs_human_review": final_state.get("needs_human_review", False),
                "hallucination_flags": final_state.get("hallucination_flags", []),
                "graph_records": final_state.get("graph_records", []),
                "retry_count": final_state.get("retry_count", 0),
                "latency_seconds": round(latency, 3),
                "thread_id": thread_id,
                "error": None,
                "error_code": None,
            }

        except asyncio.TimeoutError:
            latency = time.perf_counter() - start_ts
            logger.error(f"[{thread_id[:8]}] Agent query timed out after {timeout}s")
            return self._format_query_error(
                f"Request timed out after {timeout} seconds",
                thread_id,
                latency,
                "TIMEOUT",
            )
        except Exception as e:
            latency = time.perf_counter() - start_ts
            logger.error(
                f"[{thread_id[:8]}] Agent query failed after {latency:.3f}s: {e}",
                exc_info=True,
            )
            return self._format_query_error(str(e), thread_id, latency, "AGENT_ERROR")

    async def stream(
        self,
        question: str,
        chat_history: list[BaseMessage] | None = None,
        filter_dict: dict[str, Any] | None = None,
        workspace_id: str = "default",
        thread_id: str | None = None,
        timeout_seconds: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Streaming agent run.
        Yields node-level events in real time for frontend rendering.
        ✅ FIXED: LangGraph version compatibility + correlation_id in errors.
        """
        thread_id = thread_id or str(uuid.uuid4())

        # ✅ Validate thread_id format
        if not self._validate_thread_id(thread_id):
            logger.warning(f"Invalid thread_id format: {thread_id} — using sanitized version")
            thread_id = re.sub(r"[^a-zA-Z0-9\-_]", "", thread_id)[:64] or str(uuid.uuid4())

        timeout = timeout_seconds or _DEFAULT_TIMEOUT_SECONDS
        logger.info(f"[{thread_id[:8]}] Starting streaming query: '{question[:50]}...' | timeout={timeout}s")

        try:
            self._validate_inputs(question, workspace_id)
        except (TypeError, ValueError) as e:
            logger.warning(f"[{thread_id[:8]}] Input validation failed: {e}")
            yield {
                "type": "error",
                "message": str(e),
                "reference_id": thread_id[:8],
                "latency_seconds": 0.0,
                "error_code": "VALIDATION_ERROR",
                "correlation_id": thread_id,
            }
            return

        start_ts = time.perf_counter()
        initial_state = self._build_initial_state(question, chat_history, filter_dict, workspace_id, thread_id)
        config = self._build_graph_config(thread_id, mode="stream")

        try:
            stream_kwargs = {"config": config}
            try:
                import langgraph

                if hasattr(langgraph, "__version__"):
                    major, minor = map(int, langgraph.__version__.split(".")[:2])
                    if major >= 0 and minor >= 1:
                        stream_kwargs["version"] = "v1"  # Default in newer versions
            except Exception:
                pass  # Use default if version check fails

            stream_task = asyncio.create_task(asyncio.shield(self.graph.astream_events(initial_state, **stream_kwargs)))

            async for event in asyncio.wait_for(stream_task, timeout=timeout):
                event_name = event.get("name", "")
                event_type = event.get("event", "")

                # Node completion events
                if event_type == "on_chain_end" and event_name in {
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
                }:
                    output = event.get("data", {}).get("output", {})
                    steps = output.get("agent_steps", [])
                    step_msg = steps[-1] if steps else event_name

                    yield {
                        "type": "step",
                        "node": event_name,
                        "content": step_msg,
                        "correlation_id": thread_id,
                    }

                # Answer token streaming
                elif event_type == "on_chat_model_stream" and event_name == "answer_generator":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield {
                            "type": "token",
                            "content": chunk.content,
                            "correlation_id": thread_id,
                        }

                # Graph complete — emit citations and done
                elif event_type == "on_chain_end" and event_name == "__end__":
                    output = event.get("data", {}).get("output", {})
                    yield {
                        "type": "citations",
                        "content": output.get("citations", []),
                        "correlation_id": thread_id,
                    }
                    yield {
                        "type": "agent_summary",
                        "retrieval_route": output.get("retrieval_route", "vector"),
                        "confidence_score": output.get("confidence_score", 0.0),
                        "is_grounded": output.get("is_grounded", True),
                        "retry_count": output.get("retry_count", 0),
                        "agent_steps": output.get("agent_steps", []),
                        "correlation_id": thread_id,
                    }
                    yield {
                        "type": "done",
                        "latency_seconds": round(time.perf_counter() - start_ts, 3),
                        "correlation_id": thread_id,
                    }

            logger.info(f"[{thread_id[:8]}] Streaming query completed successfully.")

        except asyncio.TimeoutError:
            latency = time.perf_counter() - start_ts
            logger.error(f"[{thread_id[:8]}] Agent stream timed out after {timeout}s")
            yield {
                "type": "error",
                "message": f"Stream timed out after {timeout} seconds",
                "reference_id": thread_id[:8],
                "latency_seconds": round(latency, 3),
                "error_code": "TIMEOUT",
                "correlation_id": thread_id,
            }
        except Exception as e:
            latency = time.perf_counter() - start_ts
            logger.error(
                f"[{thread_id[:8]}] Agent stream failed after {latency:.3f}s: {e}",
                exc_info=True,
            )
            yield {
                "type": "error",
                "message": str(e),
                "reference_id": thread_id[:8],
                "latency_seconds": round(latency, 3),
                "error_code": "STREAM_ERROR",
                "correlation_id": thread_id,
            }

    @staticmethod
    def _format_query_error(
        error_msg: str, thread_id: str, latency: float, error_code: str = "UNKNOWN"
    ) -> dict[str, Any]:
        """DVMELTSS-E: Consistent error response for synchronous query()."""
        return {
            "success": False,
            "answer": "An error occurred while processing your request.",
            "citations": [],
            "agent_steps": [f"ERROR[{error_code}]: {error_msg}"],
            "retrieval_route": "vector",
            "confidence_score": 0.0,
            "latency_seconds": round(latency, 3),
            "error": error_msg,
            "error_code": error_code,
            "thread_id": thread_id,
            "correlation_id": thread_id,  # ✅ Added for tracing
        }

    def get_conversation_history(self, thread_id: str) -> list[BaseMessage]:
        """Retrieve conversation history for a thread from MemorySaver."""
        try:
            config = {"configurable": {"thread_id": thread_id}}
            # Check if get_state exists (LangGraph 0.0.x) or use get_state_history (0.1+)
            if hasattr(self.graph, "get_state"):
                state = self.graph.get_state(config)
                return state.values.get("chat_history", []) if state else []
            elif hasattr(self.graph, "get_state_history"):
                # Newer API: get_state_history returns iterator, take latest
                history = list(self.graph.get_state_history(config))
                if history:
                    return history[-1].values.get("chat_history", [])
            return []
        except Exception as e:
            logger.warning(f"Failed to retrieve conversation history for thread {thread_id[:8]}: {e}")
            return []

    def get_agent_metadata(self) -> dict[str, Any]:
        """✅ NEW: Return agent metadata for monitoring/debugging."""
        return {
            "graph_version": getattr(self.graph, "version", "unknown"),
            "node_count": len(getattr(self.graph, "nodes", {})),
            "checkpointing": hasattr(self.graph, "checkpointer"),
            "interrupt_before": getattr(self.graph, "interrupt_before", []),
        }


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.agent.agent_chain) ---
# ========================================================================

