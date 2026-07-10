
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

# DVMELTSS-M: Import shared LLM pool + config instead of creating duplicates
from app.config import get_settings
from app.core.llm_pool import get_llm, get_llm_for_workspace
from app.agent.state import AgentState

logger = logging.getLogger(__name__)
# env vars not configured (e.g., in tests/CI). Constants now use safe hardcoded defaults;
# any node that needs live settings calls get_settings() inline.


# DVMELTSS-S: Constant limits to prevent context overflow / prompt injection
def _get_node_limits() -> tuple[int, int, int]:
    """Lazily fetch settings-based limits. Returns (max_context, max_answer, max_history)."""
    _s = get_settings()
    return (
        getattr(_s, "agent_max_context_chars", 4000),
        getattr(_s, "agent_max_answer_chars", 6000),
        getattr(_s, "agent_max_prompt_history_chars", 1500),
    )


_MAX_CONTEXT_CHARS: int = 4000  # default; overridden per-call via _get_node_limits()
_MAX_ANSWER_CHARS: int = 6000
_MAX_PROMPT_HISTORY_CHARS: int = 1500


# ========================================================================
# -- HELPER: Robust JSON Parser for LLM Outputs (DVMELTSS-V, S) ---------
# ========================================================================
def _safe_json_parse(raw: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """
    Strip markdown fences, parse JSON, return fallback on failure.
    ✅ FIXED: Non-greedy regex + proper multiline handling.
    """
    cleaned = raw.strip()

    if "```" in cleaned:
        # Match ```json or ``` followed by JSON content (non-greedy, multiline)
        match = re.search(r"```(?:json)?\s*({[\s\S]*?})\s*```", cleaned)
        if match:
            cleaned = match.group(1)
        else:
            # Fallback: take content between first and last ```
            parts = cleaned.split("```")
            if len(parts) >= 3:
                cleaned = parts[1].strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.warning(f"LLM JSON parse failed: {e}. Raw preview: {cleaned[:150]}...")
        return fallback


# ========================================================================
# -- PYDANTIC SCHEMAS FOR STRUCTURED LLM OUTPUTS (DVMELTSS-V) -----------
# ========================================================================
class QueryAnalysisSchema(BaseModel):
    query_type: Literal["factual", "relational", "comparative", "ambiguous"]
    retrieval_route: Literal["vector", "graph", "hybrid"]
    standalone_question: str
    reasoning: str


class GradingSchema(BaseModel):
    doc_index: int
    score: float
    reason: str


class HallucinationCheckSchema(BaseModel):
    is_grounded: bool
    unsupported_claims: list[str]
    confidence: float


# ========================================================================
# -- HELPER: Validate state dict keys (DVMELTSS-V) -----------------------
# ========================================================================
def _validate_state_keys(state: AgentState, required: list[str], corr_id: str) -> tuple[bool, str]:
    """Validate that state contains required keys."""
    for key in required:
        if key not in state:
            logger.error(f"[{corr_id}] State missing required key: {key}")
            return False, f"missing key: {key}"
    return True, ""


# ========================================================================
# -- HELPER: Python 3.8-compatible thread executor (BATMAN-A) ------------
# ========================================================================
async def _run_in_thread(func, *args, **kwargs):
    """
    Run blocking function in thread pool — compatible with Python 3.8+.
    ✅ FIXED: Avoids lambda capture issues by using named function.
    """
    if sys.version_info >= (3, 9):
        return await asyncio.to_thread(func, *args, **kwargs)
    else:
        loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# ========================================================================
# NODE 1: Query Analyzer (DVMELTSS-V, S - Security)
# ========================================================================
async def node_query_analyzer(state: AgentState) -> dict:
    """
    Analyzes user question to determine query type, retrieval route, and condensed question.
    DVMELTSS-S: Context truncation prevents prompt injection/context overflow.
    """
    corr_id = state.get("correlation_id", "query_analyzer")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "query_type": "factual",
            "retrieval_route": "vector",
            "standalone_question": state.get("question", ""),
            "agent_steps": [f"QueryAnalyzer: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    question = state["question"]
    chat_history = state.get("chat_history", [])

    # Build history context with safe truncation
    history_text = ""
    if chat_history:
        raw_history = "\n".join(
            f"{'User' if isinstance(m, HumanMessage) else 'Assistant'}: {m.content}" for m in chat_history[-4:]
        )
        history_text = raw_history[:_MAX_PROMPT_HISTORY_CHARS]

    # Build history part separately to avoid f-string backslash issues
    history_part = f"Conversation history:\n{history_text}\n" if history_text else ""

    prompt = f"""Analyze this query for a document AI system.

{history_part}
Current question: {question}

Return ONLY valid JSON:
{{
  "query_type": "factual|relational|comparative|ambiguous",
  "retrieval_route": "vector|graph|hybrid",
  "standalone_question": "self-contained version of the question",
  "reasoning": "one sentence explaining routing decision"
}}
"""
    try:
        # DVMELTSS-M: Use shared LLM pool with error handling (BYOK-aware)
        try:
            llm = await get_llm_for_workspace(state.get("workspace_id", "default"), streaming=False)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get LLM: {e}")
            raise

        # Use LangChain structured output if available (DVMELTSS-V)
        if hasattr(llm, "with_structured_output"):
            structured_llm = llm.with_structured_output(QueryAnalysisSchema)
            data = await structured_llm.ainvoke([HumanMessage(content=prompt)])
            parsed = data.model_dump()
        else:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            parsed = _safe_json_parse(
                response.content,
                {
                    "query_type": "factual",
                    "retrieval_route": "vector",
                    "standalone_question": question,
                    "reasoning": "Fallback due to parse error",
                },
            )
            # Validate via Pydantic after parsing
            parsed = QueryAnalysisSchema(**parsed).model_dump()

        return {
            "query_type": parsed["query_type"],
            "retrieval_route": parsed["retrieval_route"],
            "standalone_question": parsed["standalone_question"],
            "agent_steps": [
                f"QueryAnalyzer: type={parsed['query_type']} route={parsed['retrieval_route']} | {parsed['reasoning']}"
            ],
        }
    except (ValidationError, Exception) as e:
        logger.warning(f"[{corr_id}] Query analyzer failed: {e}. Using defaults.")
        return {
            "query_type": "factual",
            "retrieval_route": "vector",
            "standalone_question": question,
            "agent_steps": [f"QueryAnalyzer: fallback to defaults ({e})"],
            "error": str(e),
            "error_code": "ANALYSIS_FAILED",
        }


# ========================================================================
# NODE 2: Vector Retriever (DVMELTSS-M, A - Async)
# ========================================================================
async def node_vector_retriever(state: AgentState) -> dict:
    """Performs vector similarity search using ChromaDB + FAISS with HyDE."""
    corr_id = state.get("correlation_id", "vector_retriever")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["standalone_question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "retrieved_docs": [],
            "agent_steps": [f"VectorRetriever: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    from app.vectorstore.store_manager import VectorStoreManager
    from app.rag.hyde import HyDEExpander

    question = state["standalone_question"]
    filter_dict = state.get("filter_dict")

    try:
        store = VectorStoreManager(workspace_id=state.get("workspace_id", "default"))
        expander = HyDEExpander()

        async def _expand_query(q: str) -> str:
            return await _run_in_thread(expander.expand, q)

        async def _search_store(q: str, k: int, f: dict | None):
            return await _run_in_thread(lambda: store.search(query=q, k=k, filter_dict=f))

        hypothesis = await _expand_query(question)
        results = await _search_store(hypothesis, 10, filter_dict)
        docs = [doc for doc, _ in results]

        return {
            "retrieved_docs": docs,
            "agent_steps": [f"VectorRetriever: {len(docs)} docs via HyDE"],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Vector retriever failed: {e}", exc_info=True)
        return {
            "retrieved_docs": [],
            "agent_steps": [f"VectorRetriever: FAILED — {e}"],
            "error": str(e),
            "error_code": "RETRIEVAL_FAILED",
        }


# ========================================================================
# NODE 3: Graph Retriever (DVMELTSS-A, E - Error handling)
# ========================================================================
async def node_graph_retriever(state: AgentState) -> dict:
    """Retrieves context from Neo4j knowledge graph using Cypher."""
    corr_id = state.get("correlation_id", "graph_retriever")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["standalone_question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "graph_context": "",
            "graph_records": [],
            "agent_steps": [f"GraphRetriever: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    from app.graph.cypher_retriever import CypherRetriever

    question = state["standalone_question"]
    workspace_id = state.get("workspace_id", "default")

    try:
        retriever = CypherRetriever()

        async def _retrieve_graph(q: str, ws: str):
            return await _run_in_thread(lambda: retriever.retrieve(query=q, workspace_id=ws, use_text_to_cypher=True))

        graph_context, graph_records = await _retrieve_graph(question, workspace_id)
        return {
            "graph_context": graph_context,
            "graph_records": graph_records,
            "agent_steps": [f"GraphRetriever: {len(graph_records)} records"],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Graph retriever failed: {e}", exc_info=True)
        return {
            "graph_context": "",
            "graph_records": [],
            "agent_steps": [f"GraphRetriever: FAILED — {e}"],
            "error": str(e),
            "error_code": "GRAPH_RETRIEVAL_FAILED",
        }


# ========================================================================
# NODE 4: Relevance Grader (DEPRECATED - Use CRAG instead)
# ========================================================================
async def node_relevance_grader(state: AgentState) -> dict:
    """
    ⚠️ DEPRECATED: Use node_crag_grader instead for Phase E.
    Kept for backward compatibility during migration.

    Grades retrieved documents for relevance. Batch grades up to 5 docs.
    """
    corr_id = state.get("correlation_id", "relevance_grader")
    logger.warning(f"[{corr_id}] node_relevance_grader called — consider migrating to node_crag_grader")

    question = state.get("standalone_question", "")
    docs = state.get("retrieved_docs", [])
    graph_ctx = state.get("graph_context", "")

    if not docs and not graph_ctx:
        return {
            "relevance_score": 0.0,
            "graded_docs": [],
            "agent_steps": ["RelevanceGrader[DEPRECATED]: no content to grade -> score=0.0"],
            "crag_action": "generate",
        }

    graded_docs = []
    total_score = 0.0
    docs_to_grade = docs[:5]

    if docs_to_grade:
        doc_snippets = "\n\n".join(f"[{i}]: {doc.page_content[:300]}" for i, doc in enumerate(docs_to_grade))
        prompt = f"""Question: {question}
Grade each document chunk for relevance. Return JSON array:
[{{"doc_index": 0, "score": 0.8, "reason": "contains payment date info"}}, ...]

Score guide: 1.0=directly answers, 0.7=partially relevant, 0.3=tangentially related, 0.0=irrelevant
Documents:
{doc_snippets}"""

        try:
            # DVMELTSS-M: Use shared LLM pool with error handling (BYOK-aware)
            try:
                llm = await get_llm_for_workspace(state.get("workspace_id", "default"), streaming=False)
            except Exception as e:
                logger.error(f"[{corr_id}] Failed to get LLM: {e}")
                raise
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            raw = response.content.strip()
            grades = _safe_json_parse(raw, [])

            # Ensure list format
            if isinstance(grades, dict):
                grades = grades.get("grades", [])

            for grade in grades:
                idx = grade.get("doc_index", 0)
                score = float(grade.get("score", 0.5))
                if 0 <= idx < len(docs_to_grade):
                    graded_docs.append(
                        {
                            "doc": docs_to_grade[idx],
                            "score": score,
                            "relevant": score >= 0.5,
                            "reason": grade.get("reason", ""),
                        }
                    )
                    total_score += score
        except Exception as e:
            logger.warning(f"[{corr_id}] Batch grading failed: {e}. Using defaults.")
            for doc in docs_to_grade:
                graded_docs.append({"doc": doc, "score": 0.6, "relevant": True, "reason": "default"})
                total_score += 0.6

    graph_bonus = 0.3 if graph_ctx and len(graph_ctx) > 100 else 0.0
    n = max(len(docs_to_grade), 1)
    aggregate_score = min((total_score / n) + graph_bonus, 1.0)

    return {
        "relevance_score": round(aggregate_score, 3),
        "graded_docs": graded_docs,
        "crag_action": "generate",
        "agent_steps": [
            f"RelevanceGrader[DEPRECATED]: score={aggregate_score:.2f} | {len([g for g in graded_docs if g['relevant']])}/{len(graded_docs)} relevant"
        ],
    }


# ========================================================================
# NODE 5: Query Rewriter (DVMELTSS-M, V)
# ========================================================================
async def node_query_rewriter(state: AgentState) -> dict:
    """Rewrites query when retrieval quality is poor. Max 2 retries."""
    corr_id = state.get("correlation_id", "query_rewriter")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["standalone_question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "agent_steps": [f"QueryRewriter: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    original = state["standalone_question"]
    retry_count = state.get("retry_count", 0)

    strategies = [
        "Rephrase using different vocabulary and be more specific",
        "Decompose into the most important single sub-question",
    ]
    strategy = strategies[min(retry_count, len(strategies) - 1)]

    prompt = f"""The following question failed to retrieve relevant documents.
Rewrite it to improve retrieval. Strategy: {strategy}
Original question: {original}
Return ONLY the rewritten question — no explanation."""

    try:
        # DVMELTSS-M: Use shared LLM pool with error handling (BYOK-aware)
        try:
            llm = await get_llm_for_workspace(state.get("workspace_id", "default"), streaming=False)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get LLM: {e}")
            raise
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        rewritten = response.content.strip().strip("\"'")
        if not rewritten:
            rewritten = original  # Fallback safety

        return {
            "standalone_question": rewritten,
            "retry_count": retry_count + 1,
            "retrieved_docs": [],  # Clear old results before retry
            "agent_steps": [f"QueryRewriter (attempt {retry_count+1}): '{original[:50]}' -> '{rewritten[:50]}'"],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Query rewriter failed: {e}")
        return {
            "retry_count": retry_count + 1,
            "agent_steps": [f"QueryRewriter: FAILED — {e}"],
            "error": str(e),
            "error_code": "REWRITE_FAILED",
        }


# ========================================================================
# NODE 6: Answer Generator (DVMELTSS-S, L, V)
# ========================================================================
async def node_answer_generator(state: AgentState) -> dict:
    """Generates final answer from context. Builds citations."""
    corr_id = state.get("correlation_id", "answer_generator")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["standalone_question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "answer": "I could not find relevant information in the documents to answer this question.",
            "citations": [],
            "confidence_score": 0.1,
            "agent_steps": [f"AnswerGenerator: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    question = state["standalone_question"]
    graded_docs = state.get("graded_docs", [])
    graph_ctx = state.get("graph_context", "")
    rel_score = state.get("relevance_score", 0.0)

    relevant_docs = [g["doc"] for g in graded_docs if g.get("relevant", True)]
    if not relevant_docs:
        relevant_docs = [g["doc"] for g in graded_docs[:3]]

    context_parts = []
    if graph_ctx:
        context_parts.append(graph_ctx)

    # DVMELTSS-M: Extract citation building to helper (could be moved to utility)
    citations = []
    for doc in relevant_docs[:5]:
        meta = doc.metadata
        sf = meta.get("source_file", "unknown")
        pg = meta.get("page_number", 0)
        content = doc.page_content[:_MAX_CONTEXT_CHARS]
        context_parts.append(f"[SOURCE: {sf}, page {pg+1}]\n{content}")
        citations.append(
            {
                "source_file": sf,
                "page_number": pg + 1,
                "block_type": meta.get("block_type", "paragraph"),
                "chunk_text": doc.page_content[:200],
            }
        )

    context = "\n\n---\n\n".join(context_parts)
    if not context.strip():
        return {
            "answer": "I could not find relevant information in the documents to answer this question.",
            "citations": [],
            "confidence_score": 0.1,
            "agent_steps": ["AnswerGenerator: no context available -> low-confidence fallback"],
            "error": None,
            "error_code": None,
        }

    system_prompt = f"""You are DocuMind AI. Answer using ONLY the provided context.
Cite sources as [SOURCE: filename, page X].
If context is insufficient, say so clearly.
Context:
{context}"""

    try:
        try:
            llm_stream = await get_llm_for_workspace(state.get("workspace_id", "default"), streaming=True)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get streaming LLM: {e}")
            raise

        answer_parts = []
        async for chunk in llm_stream.astream(
            [HumanMessage(content=f"System: {system_prompt}\n\nQuestion: {question}")]
        ):
            if chunk.content:
                answer_parts.append(chunk.content)

        answer = "".join(answer_parts)[:_MAX_ANSWER_CHARS]  # DVMELTSS-S: Limit output length

        # DVMELTSS-V: Better confidence heuristic (grounded in retrieval quality + citation presence)
        confidence = min(rel_score + (0.15 if citations else 0.0), 1.0)

        return {
            "answer": answer,
            "citations": citations,
            "confidence_score": round(confidence, 3),
            "agent_steps": [f"AnswerGenerator: {len(answer)} chars | confidence={confidence:.2f}"],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Answer generator failed: {e}", exc_info=True)
        return {
            "answer": "An error occurred generating the answer.",
            "citations": [],
            "confidence_score": 0.0,
            "agent_steps": [f"AnswerGenerator: FAILED — {e}"],
            "error": str(e),
            "error_code": "GENERATION_FAILED",
        }


# ========================================================================
# NODE 7: Hallucination Checker (DVMELTSS-S, V)
# ========================================================================
async def node_hallucination_checker(state: AgentState) -> dict:
    """Verifies generated answer is grounded in retrieved context."""
    corr_id = state.get("correlation_id", "hallucination_checker")

    answer = state.get("answer", "")
    graded = state.get("graded_docs", [])
    graph_ctx = state.get("graph_context", "")

    if not answer or answer.startswith("I could not find"):
        return {
            "is_grounded": True,
            "hallucination_flags": [],
            "needs_human_review": False,
            "agent_steps": ["HallucinationChecker: no-answer -> skip check"],
            "error": None,
            "error_code": None,
        }

    context_sample = graph_ctx[:500] if graph_ctx else ""
    for g in graded[:3]:
        context_sample += "\n" + g["doc"].page_content[:300]

    prompt = f"""Check if this answer is fully supported by the context.
Context:
{context_sample}
Answer to check:
{answer[:600]}
Return JSON:
{{
  "is_grounded": true,
  "unsupported_claims": ["claim 1 not in context", "..."],
  "confidence": 0.9
}}
is_grounded = true only if ALL factual claims are supported by context."""

    try:
        # DVMELTSS-M: Use shared LLM pool with error handling (BYOK-aware)
        try:
            llm = await get_llm_for_workspace(state.get("workspace_id", "default"), streaming=False)
        except Exception as e:
            logger.error(f"[{corr_id}] Failed to get LLM: {e}")
            raise

        if hasattr(llm, "with_structured_output"):
            data = await llm.with_structured_output(HallucinationCheckSchema).ainvoke([HumanMessage(content=prompt)])
        else:
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            raw = _safe_json_parse(
                response.content,
                {"is_grounded": True, "unsupported_claims": [], "confidence": 0.8},
            )
            data = HallucinationCheckSchema(**raw)

        is_grounded = bool(data.is_grounded)
        flags = data.unsupported_claims or []
        confidence = float(data.confidence)
        needs_review = (not is_grounded) or (confidence < 0.6)

        return {
            "is_grounded": is_grounded,
            "hallucination_flags": flags,
            "needs_human_review": needs_review,
            "confidence_score": confidence,
            "agent_steps": [
                f"HallucinationChecker: grounded={is_grounded} | flags={len(flags)} | needs_review={needs_review}"
            ],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.warning(f"[{corr_id}] Hallucination checker failed: {e}. Assuming grounded.")
        return {
            "is_grounded": True,
            "hallucination_flags": [],
            "needs_human_review": False,
            "agent_steps": [f"HallucinationChecker: error -> assume grounded ({e})"],
            "error": str(e),
            "error_code": "HALLUCINATION_CHECK_FAILED",
        }


# ========================================================================
# NODE 8: Human Review Router
# ========================================================================
async def node_human_review(state: AgentState) -> dict:
    """Flags low-confidence answers for human review."""
    corr_id = state.get("correlation_id", "human_review")

    answer = state.get("answer", "")
    flags = state.get("hallucination_flags", [])

    warning = (
        "\n\n⚠️ **Low confidence answer**: This response could not be fully "
        "verified against the source documents. "
        + (f"Potential issues: {', '.join(flags[:3])}" if flags else "")
        + " Please verify with original documents."
    )
    logger.info(f"[{corr_id}] Human review triggered for answer.")
    return {
        "answer": answer + warning,
        "agent_steps": [f"HumanReview: answer flagged | flags={flags[:3]}"],
        "error": None,
        "error_code": None,
    }


# ========================================================================
# NODE 9: CRAG Document Grader (Phase E - PRIMARY)
# ========================================================================
async def node_crag_grader(state: AgentState) -> dict:
    """CRAG document grader — PRIMARY grader for Phase E."""
    corr_id = state.get("correlation_id", "crag_grader")

    # ✅ Validate state
    is_valid, error = _validate_state_keys(state, ["standalone_question"], corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] {error}")
        return {
            "relevance_score": 0.0,
            "graded_docs": [],
            "crag_action": "rewrite",
            "agent_steps": [f"CRAGGrader: validation failed ({error})"],
            "error": error,
            "error_code": "STATE_VALIDATION_FAILED",
        }

    from app.crag import DocumentGrader

    question = state["standalone_question"]
    docs = state.get("retrieved_docs", [])

    if not docs:
        return {
            "relevance_score": 0.0,
            "graded_docs": [],
            "crag_action": "rewrite",
            "agent_steps": ["CRAGGrader: no docs to grade -> rewrite"],
            "error": None,
            "error_code": None,
        }

    try:
        grader = DocumentGrader()
        result = await grader.grade_documents(query=question, documents=docs)

        graded_docs = [
            {
                "doc": g.document,
                "score": g.score,
                "relevant": g.is_relevant,
                "reason": g.reason,
                "label": g.label.value,
            }
            for g in result.grades
        ]

        return {
            "relevance_score": result.mean_score,
            "graded_docs": graded_docs,
            "crag_action": result.crag_action,
            "missing_info": result.missing_info_summary,
            "agent_steps": [
                f"CRAGGrader: {result.relevant_count}/{len(docs)} relevant | action={result.crag_action} | mean={result.mean_score:.2f}"
            ],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] CRAG grader failed: {e}", exc_info=True)
        return {
            "relevance_score": 0.0,
            "graded_docs": [],
            "crag_action": "generate",  # Safe fallback: proceed anyway
            "agent_steps": [f"CRAGGrader: FAILED — {e}"],
            "error": str(e),
            "error_code": "CRAG_GRADING_FAILED",
        }


# ========================================================================
# NODE 10: Web Search Supplement (Phase E)
# ========================================================================
async def node_web_search(state: AgentState) -> dict:
    """Web search fallback using DuckDuckGo."""
    corr_id = state.get("correlation_id", "web_search")

    from app.crag import WebSearcher

    question = state.get("standalone_question", "")
    existing = state.get("retrieved_docs", [])
    missing_info = state.get("missing_info", "")

    search_query = f"{question} {missing_info}".strip() if missing_info else question

    try:
        searcher = WebSearcher(max_results=3)
        result = await searcher.search_async(search_query)
        new_docs = existing + result.documents

        return {
            "retrieved_docs": new_docs,
            "web_search_used": True,
            "agent_steps": [
                f"WebSearch: '{search_query[:50]}' -> {result.result_count} results | total_docs={len(new_docs)}"
            ],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Web search failed: {e}", exc_info=True)
        return {
            "retrieved_docs": existing,
            "web_search_used": False,
            "agent_steps": [f"WebSearch: FAILED — {e}"],
            "error": str(e),
            "error_code": "WEB_SEARCH_FAILED",
        }


# ========================================================================
# NODE 11: Query Decomposer (Phase E)
# ========================================================================
async def node_query_decomposer(state: AgentState) -> dict:
    """Decomposes ambiguous queries into sub-questions."""
    corr_id = state.get("correlation_id", "query_decomposer")

    from app.crag import QueryDecomposer

    question = state.get("standalone_question", "")
    graded = state.get("graded_docs", [])
    retry_count = state.get("retry_count", 0)

    context_summary = "\n".join(g["doc"].page_content[:150] for g in graded[:3])

    try:
        decomposer = QueryDecomposer()
        decomposed = await decomposer.decompose(question, context_summary)

        return {
            "sub_questions": decomposed.sub_questions,
            "decomposed_query": decomposed.original,
            "retry_count": retry_count + 1,
            "agent_steps": [
                f"QueryDecomposer: '{question[:40]}' -> {len(decomposed.sub_questions)} sub-questions | {decomposed.decomposition_reasoning[:60]}"
            ],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Query decomposer failed: {e}", exc_info=True)
        return {
            "sub_questions": [question],
            "decomposed_query": question,
            "retry_count": retry_count + 1,
            "agent_steps": [f"QueryDecomposer: FAILED — {e}"],
            "error": str(e),
            "error_code": "DECOMPOSITION_FAILED",
        }


# ========================================================================
# NODE 12: Self-RAG Reflector (Phase E)
# ========================================================================
async def node_self_rag_reflector(state: AgentState) -> dict:
    """Reflects on generated answer and decides whether to retrieve more."""
    corr_id = state.get("correlation_id", "self_rag_reflector")

    from app.crag import SelfRAGReflector

    question = state.get("standalone_question", "")
    answer = state.get("answer", "")
    graded = state.get("graded_docs", [])
    retry_count = state.get("retry_count", 0)

    context_docs = [g["doc"] for g in graded if g.get("relevant")]
    MAX_SELF_RAG_RETRIES = 1

    try:
        reflector = SelfRAGReflector()
        assessment = await reflector.reflect(question=question, answer=answer, context_docs=context_docs)

        should_retrieve = (
            assessment.retrieve_more and retry_count < MAX_SELF_RAG_RETRIES and bool(assessment.additional_queries)
        )

        new_question = (
            assessment.additional_queries[0] if should_retrieve and assessment.additional_queries else question
        )

        return {
            "self_rag_retrieve_more": should_retrieve,
            "self_rag_confidence": assessment.confidence,
            "self_rag_supported": assessment.is_supported,
            "self_rag_complete": assessment.is_complete,
            "self_rag_notes": assessment.reflection_notes,
            "standalone_question": new_question,
            "confidence_score": assessment.confidence,
            "retry_count": retry_count + (1 if should_retrieve else 0),
            "retrieved_docs": [] if should_retrieve else state.get("retrieved_docs", []),
            "agent_steps": [
                f"SelfRAG: supported={assessment.is_supported} | complete={assessment.is_complete} | retrieve_more={should_retrieve}"
            ],
            "error": None,
            "error_code": None,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Self-RAG reflector failed: {e}", exc_info=True)
        return {
            "self_rag_retrieve_more": False,
            "self_rag_confidence": 0.5,
            "self_rag_supported": True,
            "self_rag_complete": True,
            "self_rag_notes": f"SelfRAG error: {e}",
            "agent_steps": [f"SelfRAG: FAILED -> {e}"],
            "error": str(e),
            "error_code": "SELF_RAG_FAILED",
        }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "node_query_analyzer",
    "node_vector_retriever",
    "node_graph_retriever",
    "node_relevance_grader",
    "node_crag_grader",
    "node_query_rewriter",
    "node_web_search",
    "node_query_decomposer",
    "node_answer_generator",
    "node_self_rag_reflector",
    "node_hallucination_checker",
    "node_human_review",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.agent.nodes) ---------
# ========================================================================

