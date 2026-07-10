
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, Literal
from typing_extensions import TypedDict, NotRequired

from langchain_core.documents import Document
from langchain_core.messages import BaseMessage

# DVMELTSS-V: Optional Pydantic model for runtime validation in production
try:
    from pydantic import BaseModel, Field, field_validator, model_validator

    _PYDANTIC_AVAILABLE = True
except ImportError:
    _PYDANTIC_AVAILABLE = False


# ========================================================================
# -- HELPER: Sanitize filter_dict for JSON serialization (DVMELTSS-S) ---
# ========================================================================
def _sanitize_filter_dict(filter_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Remove non-serializable values from filter_dict.
    ✅ Keeps only JSON-safe types: str, int, float, bool, None, list, dict.
    """
    safe = {}
    for k, v in filter_dict.items():
        if isinstance(v, (str, int, float, bool, type(None))):
            safe[k] = v
        elif isinstance(v, (list, dict)):
            # Recursively sanitize nested structures
            try:
                import json

                json.dumps(v)  # Test serializability
                safe[k] = v
            except (TypeError, ValueError):
                # Fallback: convert to string representation
                safe[k] = str(v)
        # Skip functions, classes, etc.
    return safe


class AgentState(TypedDict, total=False):
    """
    Shared state object passed between all agent nodes.

    Design principles (DVMELTSS-D):
    - Every field has a clear owner (which node writes it)
    - Lists use operator.add for safe parallel merging
    - Optional fields use NotRequired for explicit typing
    - total=False -> all fields optional by default (safe for partial updates)

    ASCALE-L: Clear layered flow: Input -> Analysis -> Retrieval -> Grading -> Generation -> Validation
    """

    correlation_id: NotRequired[str]  # [OWNER: API] request ID for end-to-end tracing

    # ========================================================================
    # -- INPUT LAYER ---------------------------------------------------------
    # ========================================================================
    question: str  # [OWNER: API] original user question — REQUIRED
    chat_history: list[BaseMessage]  # [OWNER: API] conversation history
    workspace_id: str  # [OWNER: API] tenant namespace — REQUIRED
    filter_dict: NotRequired[dict[str, Any]]  # [OWNER: API] metadata filters (sanitized)

    # ========================================================================
    # -- ANALYSIS LAYER ------------------------------------------------------
    # ========================================================================
    query_type: NotRequired[Literal["factual", "relational", "comparative", "ambiguous"]]
    retrieval_route: NotRequired[Literal["vector", "graph", "hybrid"]]
    standalone_question: str  # [OWNER: query_analyzer] condensed/rewritten question

    # ========================================================================
    # -- RETRIEVAL LAYER -----------------------------------------------------
    # ========================================================================
    retrieved_docs: Annotated[list[Document], operator.add]  # [OWNER: vector/graph retrievers]
    graph_context: NotRequired[str]  # [OWNER: graph_retriever] Neo4j context text
    graph_records: NotRequired[list[dict]]  # [OWNER: graph_retriever] raw records for viz

    # ========================================================================
    # -- GRADING LAYER -------------------------------------------------------
    # ========================================================================
    relevance_score: NotRequired[float]  # [OWNER: relevance_grader/crag_grader] 0.0–1.0
    graded_docs: NotRequired[list[dict]]  # [OWNER: grader] [{doc, score, relevant, reason}]
    retry_count: NotRequired[int]  # [OWNER: query_rewriter] retrieval retry counter

    # ========================================================================
    # -- GENERATION LAYER ----------------------------------------------------
    # ========================================================================
    answer: NotRequired[str]  # [OWNER: answer_generator]
    citations: NotRequired[list[dict]]  # [OWNER: answer_generator] source metadata
    confidence_score: NotRequired[float]  # [OWNER: answer_generator/self_rag] 0.0–1.0

    # ========================================================================
    # -- VALIDATION LAYER ----------------------------------------------------
    # ========================================================================
    is_grounded: NotRequired[bool]  # [OWNER: hallucination_checker]
    hallucination_flags: NotRequired[list[str]]  # [OWNER: hallucination_checker] unsupported claims

    # ========================================================================
    # -- CONTROL FLOW --------------------------------------------------------
    # ========================================================================
    needs_human_review: NotRequired[bool]  # [OWNER: human_review/hallucination_checker]
    agent_steps: Annotated[list[str], operator.add]  # [OWNER: ALL] audit trail — merged safely
    error: NotRequired[str]  # [OWNER: ANY] error message if node failed
    error_code: NotRequired[str]  # [OWNER: ANY] structured error code for frontend handling

    # ========================================================================
    # -- CRAG EXTENSIONS (Phase E) — NOW INSIDE TypedDict ✅ -----------------
    # ========================================================================
    crag_action: NotRequired[Literal["generate", "filter_and_supplement", "rewrite", "decompose"]]
    missing_info: NotRequired[str]  # [OWNER: crag_grader] what was missing from docs
    web_search_used: NotRequired[bool]  # [OWNER: web_search] fallback indicator
    sub_questions: NotRequired[list[str]]  # [OWNER: query_decomposer] decomposed queries
    decomposed_query: NotRequired[str]  # [OWNER: query_decomposer] original before decomposition

    # ========================================================================
    # -- SELF-RAG EXTENSIONS (Phase E) — NOW INSIDE TypedDict ✅ -------------
    # ========================================================================
    self_rag_retrieve_more: NotRequired[bool]  # [OWNER: self_rag_reflector]
    self_rag_confidence: NotRequired[float]  # [OWNER: self_rag_reflector] 0.0–1.0
    self_rag_supported: NotRequired[bool]  # [OWNER: self_rag_reflector] grounded in context?
    self_rag_complete: NotRequired[bool]  # [OWNER: self_rag_reflector] fully addresses question?
    self_rag_notes: NotRequired[str]  # [OWNER: self_rag_reflector] reflection notes


# DVMELTSS-V: Optional Pydantic model for runtime validation (if pydantic available)
if _PYDANTIC_AVAILABLE:

    class AgentStateModel(BaseModel):
        """
        Pydantic model for runtime validation of AgentState.
        Use in production for stricter type checking.

        ✅ FIXED: Pydantic v2 syntax + aligned defaults with TypedDict.

        Usage:
            validated = AgentStateModel(**state_dict)
            state = validated.model_dump()
        """

        # Input Layer
        question: str = Field(..., min_length=3, max_length=2000)
        chat_history: list[BaseMessage] = Field(default_factory=list)
        workspace_id: str = Field(..., min_length=1, max_length=64)
        filter_dict: dict[str, Any] = Field(default_factory=dict)

        # Analysis Layer — ✅ FIXED: Defaults aligned with TypedDict
        query_type: Literal["factual", "relational", "comparative", "ambiguous"] = "factual"
        retrieval_route: Literal["vector", "graph", "hybrid"] = "vector"
        standalone_question: str = Field(default="", min_length=3)  # ✅ Allow empty for validation

        # Retrieval Layer
        retrieved_docs: list[Document] = Field(default_factory=list)
        graph_context: str = ""
        graph_records: list[dict] = Field(default_factory=list)

        # Grading Layer — ✅ FIXED: Range validation
        relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
        graded_docs: list[dict] = Field(default_factory=list)
        retry_count: int = Field(default=0, ge=0, le=5)

        # Generation Layer
        answer: str = ""
        citations: list[dict] = Field(default_factory=list)
        confidence_score: float = Field(default=0.0, ge=0.0, le=1.0)

        # Validation Layer
        is_grounded: bool = True
        hallucination_flags: list[str] = Field(default_factory=list)

        # Control Flow
        needs_human_review: bool = False
        agent_steps: list[str] = Field(default_factory=list)
        error: Optional[str] = None
        error_code: Optional[str] = None

        # CRAG Extensions
        crag_action: Literal["generate", "filter_and_supplement", "rewrite", "decompose"] = "generate"
        missing_info: str = ""
        web_search_used: bool = False
        sub_questions: list[str] = Field(default_factory=list)
        decomposed_query: str = ""

        # Self-RAG Extensions
        self_rag_retrieve_more: bool = False
        self_rag_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
        self_rag_supported: bool = True
        self_rag_complete: bool = True
        self_rag_notes: str = ""

        @field_validator("question", "standalone_question", mode="before")
        @classmethod
        def strip_strings(cls, v: Any) -> Any:
            return v.strip() if isinstance(v, str) else v

        @field_validator("relevance_score", "confidence_score", "self_rag_confidence", mode="before")
        @classmethod
        def clamp_scores(cls, v: Any) -> float:
            if not isinstance(v, (int, float)):
                return 0.0
            return max(0.0, min(1.0, float(v)))

        @model_validator(mode="before")
        @classmethod
        def sanitize_filters(cls, data: Any) -> Any:
            if isinstance(data, dict) and "filter_dict" in data:
                data["filter_dict"] = _sanitize_filter_dict(data["filter_dict"])
            return data


# DVMELTSS-T: Helper for testing — validate state schema at runtime if needed
def validate_agent_state_minimal(state: dict) -> tuple[bool, Optional[str]]:
    """
    Quick runtime validation for critical required fields.
    Returns (is_valid, error_message).

    Usage in tests: backend/app/tests/test_agent.py
    """
    required = {"question", "workspace_id", "standalone_question"}
    missing = required - state.keys()
    if missing:
        return False, f"Missing required fields: {missing}"
    if not isinstance(state.get("question"), str) or not state["question"].strip():
        return False, "Question must be non-empty string"
    if not isinstance(state.get("workspace_id"), str) or not state["workspace_id"].strip():
        return False, "workspace_id must be non-empty string"

    # ✅ Validate score ranges if present
    for field in ["relevance_score", "confidence_score", "self_rag_confidence"]:
        val = state.get(field)
        if val is not None and not (0.0 <= float(val) <= 1.0):
            return False, f"{field} must be between 0.0 and 1.0, got {val}"

    return True, None


# DVMELTSS-V: Optional strict validation using Pydantic (if available)
def validate_agent_state_strict(state: dict) -> tuple[bool, Optional[str]]:
    """
    Strict runtime validation using Pydantic model (if available).
    Returns (is_valid, error_message or None).

    Usage: Production environments where type safety is critical.
    """
    if not _PYDANTIC_AVAILABLE:
        return validate_agent_state_minimal(state)

    try:
        AgentStateModel(**state)
        return True, None
    except Exception as e:
        return False, f"Pydantic validation failed: {e}"


def state_to_dict(state: AgentState) -> dict[str, Any]:
    """Convert AgentState to JSON-serializable dict."""
    result = dict(state)
    # Sanitize filter_dict if present
    if "filter_dict" in result:
        result["filter_dict"] = _sanitize_filter_dict(result["filter_dict"])
    return result


def state_from_dict(data: dict[str, Any]) -> AgentState:
    """Convert dict to AgentState with minimal validation."""
    # Start with required fields
    state: AgentState = {
        "question": data.get("question", ""),
        "workspace_id": data.get("workspace_id", ""),
        "standalone_question": data.get("standalone_question", ""),
    }
    # Add optional fields if present and valid
    for key in [
        "correlation_id",
        "chat_history",
        "filter_dict",
        "query_type",
        "retrieval_route",
        "retrieved_docs",
        "graph_context",
        "graph_records",
        "relevance_score",
        "graded_docs",
        "retry_count",
        "answer",
        "citations",
        "confidence_score",
        "is_grounded",
        "hallucination_flags",
        "needs_human_review",
        "agent_steps",
        "error",
        "error_code",
        "crag_action",
        "missing_info",
        "web_search_used",
        "sub_questions",
        "decomposed_query",
        "self_rag_retrieve_more",
        "self_rag_confidence",
        "self_rag_supported",
        "self_rag_complete",
        "self_rag_notes",
    ]:
        if key in data:
            state[key] = data[key]  # type: ignore
    return state


def get_required_fields() -> list[str]:
    """✅ NEW: Return list of required state fields for API documentation."""
    return ["question", "workspace_id", "standalone_question"]


# DVMELTSS-M: Explicit module exports
__all__ = [
    "AgentState",
    "validate_agent_state_minimal",
    "validate_agent_state_strict",
    "state_to_dict",
    "state_from_dict",
    "get_required_fields",
]
if _PYDANTIC_AVAILABLE:
    __all__.append("AgentStateModel")

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.agent.state) ---------
# ========================================================================

