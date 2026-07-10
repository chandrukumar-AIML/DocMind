
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Final, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.core.llm_pool import get_llm
from app.core.prompts import (
    escape_prompt_content,
    estimate_tokens_approx,
    build_safe_prompt,
)
from app.core.retry import retry_async, RetryConfig, CircuitBreaker

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-A) -------------------------
# ========================================================================

# DVMELTSS-V: Validation constraints for reflection output
_MIN_CONFIDENCE: Final = 0.0
_MAX_CONFIDENCE: Final = 1.0
_MAX_ADDITIONAL_QUERIES: Final = 3
_MAX_REFLECTION_NOTES_LENGTH: Final = 300

# Retry configuration for transient LLM errors
_REFLECTION_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=2,
    backoff_base=1.0,
    exceptions=(Exception,),
)

# Circuit breaker config to prevent cascade failures
_REFLECTION_CIRCUIT_BREAKER: Final = {
    "failure_threshold": 5,
    "recovery_timeout": 30.0,  # seconds
}


# DVMELTSS-V: Pydantic schema for structured LLM output
class SelfRAGResponseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_supported: bool
    is_complete: bool
    retrieve_more: bool
    confidence: float = Field(..., ge=_MIN_CONFIDENCE, le=_MAX_CONFIDENCE)
    reflection_notes: str
    additional_queries: list[str] = Field(default_factory=list, max_length=_MAX_ADDITIONAL_QUERIES)


@dataclass  # FIXED: Removed frozen=True to allow safe mutation in __post_init__
class SelfRAGAssessment:
    """
    Result of Self-RAG reflection on a generated answer.
    FIXED: Not frozen to allow safe truncation in __post_init__.
    """

    answer: str
    retrieve_more: bool
    is_supported: bool
    is_complete: bool
    confidence: float
    reflection_notes: str
    additional_queries: list[str] = field(default_factory=list)

    def __post_init__(self):
        # DVMELTSS-V: Validate confidence range
        if not (_MIN_CONFIDENCE <= self.confidence <= _MAX_CONFIDENCE):
            raise ValueError(f"Confidence out of range: {self.confidence}")
        if len(self.reflection_notes) > _MAX_REFLECTION_NOTES_LENGTH:
            self.reflection_notes = self.reflection_notes[:_MAX_REFLECTION_NOTES_LENGTH] + "..."
        if len(self.additional_queries) > _MAX_ADDITIONAL_QUERIES:
            self.additional_queries = self.additional_queries[:_MAX_ADDITIONAL_QUERIES]

    @property
    def needs_improvement(self) -> bool:
        """Convenience: True if answer needs more retrieval or correction."""
        return not self.is_supported or not self.is_complete or self.retrieve_more

    def to_dict(self) -> dict:
        """Serialize for logging/API responses."""
        return {
            "retrieve_more": self.retrieve_more,
            "is_supported": self.is_supported,
            "is_complete": self.is_complete,
            "confidence": self.confidence,
            "reflection_notes": self.reflection_notes,
            "additional_queries": self.additional_queries,
        }


@dataclass(frozen=True)
class CRAGDecision:
    """
    Immutable complete CRAG routing decision combining grading + self-RAG results.
    This is the single object that drives the agent's control flow.
    """

    # From document grader
    grading_action: str  # generate / filter_and_supplement / rewrite / decompose
    relevant_docs: list[Document]
    relevant_ratio: float
    missing_info: str

    # From self-RAG (if generation happened)
    self_rag_assessment: Optional[SelfRAGAssessment] = None

    # Web search supplement
    web_docs: list[Document] = field(default_factory=list)

    # Final merged context
    final_context_docs: list[Document] = field(default_factory=list)

    # Confidence score for the overall pipeline run
    pipeline_confidence: float = 0.0

    @property
    def should_proceed_to_generation(self) -> bool:
        """DVMELTSS-M: Clear decision logic for pipeline routing."""
        if self.grading_action == "generate" and self.self_rag_assessment:
            return self.self_rag_assessment.is_supported and self.self_rag_assessment.is_complete
        return self.grading_action in ("generate", "filter_and_supplement")


class SelfRAGReflector:
    """
    Implements Self-RAG reflection: assesses generated answers and
    decides whether more retrieval is needed.

    The reflection has three questions:
    1. Is the answer grounded in the retrieved context?
    2. Does the answer fully address the question?
    3. Is additional retrieval needed for any part?

    Features (DVMELTSS-V, BATMAN-A, E):
    - Structured JSON output via Pydantic — reliable parsing
    - Token counting via centralized utils to prevent context window overflow
    - Retry logic for transient LLM errors
    - Circuit breaker to prevent cascade failures
    - Prompt escaping via centralized utils to prevent injection
    - Correlation ID support for distributed tracing
    """

    REFLECTION_PROMPT_TEMPLATE = """Assess this answer to determine if it needs improvement.

Question: {question}

Retrieved context (summary):
{context_summary}

Generated answer:
{answer}

Return ONLY valid JSON matching this schema:
{{
  "is_supported": true,
  "is_complete": true,
  "retrieve_more": false,
  "confidence": 0.85,
  "reflection_notes": "answer is well-grounded and complete",
  "additional_queries": []
}}

Field definitions:
- is_supported:      every factual claim in the answer is backed by context
- is_complete:       answer fully addresses all parts of the question
- retrieve_more:     true if additional retrieval would meaningfully improve the answer
- confidence:        0.0–1.0 overall quality score
- reflection_notes:  specific issues found, or "looks good" if none
- additional_queries: specific queries to retrieve missing information
                      (empty list if retrieve_more=false)
"""

    def __init__(self, model: str = "gpt-4o"):
        self.llm = get_llm(streaming=False, model_override=model, temperature_override=0.0)
        # Pre-check if structured output is supported
        self._use_structured_output = hasattr(self.llm, "with_structured_output")

        # DVMELTSS-E: Retry decorator for LLM calls
        self._llm_retry = retry_async(config=_REFLECTION_RETRY_CONFIG)

        # DVMELTSS-S: Circuit breaker for repeated failures
        self._circuit_breaker = CircuitBreaker(
            name="self_rag_reflection",
            **_REFLECTION_CIRCUIT_BREAKER,
        )

    def _build_context_summary(self, context_docs: list[Document], max_chars: int) -> str:
        """
        Build a concise context summary for the reflection prompt.
        BATMAN-M: Truncates intelligently to fit token budget.
        """
        if not context_docs:
            return "No context retrieved."

        parts = []
        total_chars = 0
        for doc in context_docs[:3]:  # Limit to top 3 docs
            sf = doc.metadata.get("source_file", "unknown")
            pg = doc.metadata.get("page_number", 0) + 1
            content = doc.page_content[:300]  # Per-doc limit
            snippet = f"[{sf}, p.{pg}]: {escape_prompt_content(content)}"  # FIXED: Use centralized escape
            if total_chars + len(snippet) > max_chars:
                break
            parts.append(snippet)
            total_chars += len(snippet)

        return "\n\n".join(parts) if parts else "No context."

    async def reflect(
        self,
        question: str,
        answer: str,
        context_docs: list[Document],
        max_context_chars: int = 800,
        correlation_id: Optional[str] = None,
    ) -> SelfRAGAssessment:
        """
        Reflect on a generated answer with retry logic + circuit breaker for resilience.

        Args:
            question: original user question
            answer: generated answer to assess
            context_docs: documents used to generate the answer
            max_context_chars: max chars of context to include (token-aware)
            correlation_id: Request ID for distributed tracing

        Returns:
            SelfRAGAssessment with routing decision
        """
        corr_id = correlation_id or "unknown"

        # Handle empty/failed answers early
        if not answer or answer.startswith("I could not find"):
            logger.debug(f"[{corr_id}] SelfRAG: skipping reflection for empty answer")
            return SelfRAGAssessment(
                answer=answer,
                retrieve_more=True,
                is_supported=False,
                is_complete=False,
                confidence=0.1,
                reflection_notes="No answer generated — need retrieval.",
                additional_queries=[question[:_MAX_ADDITIONAL_QUERIES]],
            )

        # Build context summary with token-aware truncation
        context_summary = self._build_context_summary(context_docs, max_context_chars)

        prompt_template_tokens = estimate_tokens_approx(self.REFLECTION_PROMPT_TEMPLATE)
        question_tokens = estimate_tokens_approx(question)
        answer_tokens = estimate_tokens_approx(answer)
        context_tokens = estimate_tokens_approx(context_summary)

        total_estimated = prompt_template_tokens + question_tokens + answer_tokens + context_tokens
        if total_estimated > 6000:
            logger.warning(
                f"[{corr_id}] Reflection prompt approaching token limit ({total_estimated}/6000) — truncating inputs"
            )
            # Truncate answer more aggressively
            answer = answer[:600]
            context_summary = context_summary[:400]

        prompt = build_safe_prompt(
            self.REFLECTION_PROMPT_TEMPLATE,
            question=question,
            context_summary=context_summary,
            answer=answer,
        )

        # DVMELTSS-E: Circuit breaker + retry logic for transient LLM errors
        try:
            async with self._circuit_breaker:
                return await self._do_reflect(prompt, question, answer, corr_id)
        except RuntimeError as e:
            # Circuit breaker is OPEN — fail fast
            logger.warning(f"[{corr_id}] SelfRAG circuit breaker OPEN: {e}")
            return SelfRAGAssessment(
                answer=answer,
                retrieve_more=False,
                is_supported=True,
                is_complete=True,
                confidence=0.5,
                reflection_notes="reflection unavailable: circuit breaker open",
                additional_queries=[],
            )
        except Exception as e:
            # Fallback on all retries exhausted
            logger.warning(f"[{corr_id}] SelfRAG reflection failed after retries: {e}")
            return SelfRAGAssessment(
                answer=answer,
                retrieve_more=False,
                is_supported=True,
                is_complete=True,
                confidence=0.7,
                reflection_notes=f"reflection unavailable after error: {e}",
                additional_queries=[],
            )

    async def _do_reflect(
        self,
        prompt: str,
        original_question: str,
        original_answer: str,
        correlation_id: str,
    ) -> SelfRAGAssessment:
        """Internal: perform the actual LLM call and parse response."""
        corr_id = correlation_id

        try:
            # DVMELTSS-V: Use structured output if available (more reliable than JSON parsing)
            if self._use_structured_output:
                structured_llm = self.llm.with_structured_output(SelfRAGResponseSchema)
                response = await self._llm_retry(lambda: structured_llm.ainvoke([HumanMessage(content=prompt)]))
                data = response.model_dump()
            else:
                response = await self._llm_retry(lambda: self.llm.ainvoke([HumanMessage(content=prompt)]))
                raw = response.content.strip()
                # Strip markdown fences if present
                if "```" in raw:
                    raw = raw.split("```")[1].lstrip("json").strip()
                data = json.loads(raw)
                # Validate via Pydantic after parsing
                SelfRAGResponseSchema.model_validate(data)

            assessment = SelfRAGAssessment(
                answer=original_answer,
                retrieve_more=bool(data.get("retrieve_more", False)),
                is_supported=bool(data.get("is_supported", True)),
                is_complete=bool(data.get("is_complete", True)),
                confidence=float(data.get("confidence", 0.7)),
                reflection_notes=str(data.get("reflection_notes", ""))[:_MAX_REFLECTION_NOTES_LENGTH],
                additional_queries=[
                    q.strip() for q in data.get("additional_queries", [])[:_MAX_ADDITIONAL_QUERIES] if q.strip()
                ],
            )

            logger.info(
                f"[{corr_id}] SelfRAG: supported={assessment.is_supported} | "
                f"complete={assessment.is_complete} | "
                f"retrieve_more={assessment.retrieve_more} | "
                f"confidence={assessment.confidence:.2f} | "
                f"notes={assessment.reflection_notes[:50]}..."
            )
            return assessment

        except (json.JSONDecodeError, ValidationError):
            # Re-raise to trigger retry/circuit breaker logic in caller
            raise
        except Exception:
            # Re-raise to trigger retry/circuit breaker logic in caller
            raise


# DVMELTSS-M: Explicit module exports
__all__ = ["SelfRAGReflector", "SelfRAGAssessment", "CRAGDecision"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.crag.self_rag) -------
# ========================================================================

