
from __future__ import annotations

import asyncio  # FIXED: Added missing import
import json
import logging
from dataclasses import dataclass
from typing import Final, Optional

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.prompts import estimate_tokens_approx, build_safe_prompt
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-A) -------------------------
# ========================================================================

# DVMELTSS-V: Validation constraints for sub-questions
_MIN_SUBQUESTION_LENGTH: Final = 10
_MAX_SUBQUESTIONS: Final = 4
_MAX_QUESTION_LENGTH: Final = 200

# Retry configuration for transient LLM errors
_DECOMPOSE_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=2,
    backoff_base=0.5,
    exceptions=(Exception,),
)


# DVMELTSS-V: Pydantic schema for structured LLM output
class DecompositionResponseSchema(BaseModel):
    sub_questions: list[str] = Field(..., min_length=1, max_length=4)
    reasoning: str

    class Config:
        extra = "forbid"  # Reject unexpected fields


@dataclass(frozen=True)
class DecomposedQuery:
    """
    Immutable result of query decomposition.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    original: str
    sub_questions: list[str]
    decomposition_reasoning: str = ""
    is_decomposed: bool = True

    def __post_init__(self):
        # DVMELTSS-V: Validate sub-questions on construction
        for i, q in enumerate(self.sub_questions):
            if not q or len(q.strip()) < _MIN_SUBQUESTION_LENGTH:
                raise ValueError(f"Sub-question {i} too short: '{q}'")
            if len(q) > _MAX_QUESTION_LENGTH:
                raise ValueError(f"Sub-question {i} too long: '{q[:50]}...'")

    @property
    def is_simple(self) -> bool:
        """True if decomposition produced only 1 sub-question (already simple)."""
        return len(self.sub_questions) <= 1

    def to_dict(self) -> dict:
        """Serialize for logging/API responses."""
        return {
            "original": self.original,
            "sub_questions": self.sub_questions,
            "reasoning": self.decomposition_reasoning,
            "is_decomposed": self.is_decomposed,
        }


class QueryDecomposer:
    """
    Decomposes complex queries into simpler sub-questions for CRAG.

    Used when grading returns "ambiguous" — the query likely spans
    multiple topics and needs to be answered in parts.

    Features (DVMELTSS-V, BATMAN-A):
    - Structured JSON output via Pydantic — reliable parsing
    - Token counting via centralized utils to prevent context window overflow
    - Case-insensitive deduplication of sub-questions
    - Prompt escaping via centralized utils to prevent injection
    - Retry logic for transient LLM errors
    - Correlation ID support for distributed tracing
    """

    DECOMPOSE_PROMPT_TEMPLATE = """You are a query analysis expert for a document AI system.
Break this complex query into 2-4 simpler sub-questions that can each be
answered independently by searching a document database.

Query: {query}

Context (documents found but graded as ambiguous):
{context_summary}

Return ONLY valid JSON matching this schema:
{{
  "sub_questions": [
    "specific sub-question 1",
    "specific sub-question 2"
  ],
  "reasoning": "why this decomposition helps"
}}

Rules:
- Each sub-question must be self-contained and searchable
- Sub-questions should cover different aspects of the original
- Maximum 4 sub-questions
- If query is already simple, return it as the only sub-question
- Sub-questions should be concrete, not abstract
- Avoid duplicate or near-duplicate questions
"""

    def __init__(self, model: str = "gpt-4o"):
        settings = get_settings()
        self.llm = ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            temperature=0,
            streaming=False,
        )
        # Pre-check if structured output is supported
        self._use_structured_output = hasattr(self.llm, "with_structured_output")

        # DVMELTSS-E: Retry decorator for LLM calls
        self._llm_retry = retry_async(config=_DECOMPOSE_RETRY_CONFIG)

    def _deduplicate_questions(self, questions: list[str]) -> list[str]:
        """
        DVMELTSS-V: Case-insensitive deduplication while preserving order.
        Also filters out questions that are too short or too similar.
        """
        seen_normalized: set[str] = set()
        deduped: list[str] = []

        for q in questions:
            normalized = q.lower().strip()
            # Skip empty or too-short questions
            if len(normalized) < _MIN_SUBQUESTION_LENGTH:
                continue
            # Skip if already seen (case-insensitive)
            if normalized in seen_normalized:
                continue
            # Skip if too similar to existing (simple heuristic: prefix match)
            if any(
                normalized.startswith(existing[:20]) or existing.startswith(normalized[:20])
                for existing in seen_normalized
            ):
                continue

            seen_normalized.add(normalized)
            deduped.append(q.strip())
            if len(deduped) >= _MAX_SUBQUESTIONS:
                break

        return deduped

    async def decompose(
        self,
        query: str,
        context_summary: str = "",
        correlation_id: Optional[str] = None,
    ) -> DecomposedQuery:
        """
        Decompose a complex query into sub-questions.

        Args:
            query: original user query
            context_summary: brief summary of what was retrieved
            correlation_id: Request ID for distributed tracing

        Returns:
            DecomposedQuery with validated sub_questions list
        """
        corr_id = correlation_id or "unknown"

        # Truncate inputs to prevent token overflow using centralized utils
        safe_query = query[:_MAX_QUESTION_LENGTH]
        safe_context = context_summary[:1000]  # Reasonable limit for context

        prompt_template_tokens = estimate_tokens_approx(self.DECOMPOSE_PROMPT_TEMPLATE)
        query_tokens = estimate_tokens_approx(safe_query)
        context_tokens = estimate_tokens_approx(safe_context)

        if prompt_template_tokens + query_tokens + context_tokens > 6000:
            logger.warning(f"[{corr_id}] Decomposition prompt approaching token limit — truncating context")
            safe_context = safe_context[:500]

        prompt = build_safe_prompt(
            self.DECOMPOSE_PROMPT_TEMPLATE,
            query=safe_query,
            context_summary=safe_context or "No context retrieved yet.",
        )

        try:
            # DVMELTSS-V: Use structured output if available (more reliable than JSON parsing)
            if self._use_structured_output:
                structured_llm = self.llm.with_structured_output(DecompositionResponseSchema)
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
                DecompositionResponseSchema.model_validate(data)

            sub_questions = data.get("sub_questions", [])

            # DVMELTSS-V: Validate and clean sub-questions
            if not sub_questions:
                sub_questions = [safe_query]

            cleaned = self._deduplicate_questions(sub_questions)

            # Fallback: if all questions filtered out, use original
            if not cleaned:
                cleaned = [safe_query]

            result = DecomposedQuery(
                original=safe_query,
                sub_questions=cleaned[:_MAX_SUBQUESTIONS],
                decomposition_reasoning=data.get("reasoning", "")[:200],  # Truncate reasoning
                is_decomposed=len(cleaned) > 1,
            )

            logger.info(
                f"[{corr_id}] QueryDecomposer: '{safe_query[:50]}' -> "
                f"{len(result.sub_questions)} sub-questions | "
                f"decomposed={result.is_decomposed}"
            )
            return result

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[{corr_id}] Query decomposition JSON parse/validation failed: {e}. Using original.")
            return DecomposedQuery(
                original=safe_query,
                sub_questions=[safe_query],
                decomposition_reasoning=f"parse error: {e}",
                is_decomposed=False,
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Query decomposition LLM call failed: {e}")
            return DecomposedQuery(
                original=safe_query,
                sub_questions=[safe_query],
                decomposition_reasoning=f"LLM error: {e}",
                is_decomposed=False,
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["QueryDecomposer", "DecomposedQuery"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.crag.query_decomposer) -
# ========================================================================

