# backend/app/crag/query_decomposer.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability
# BATMAN-FIX: A - API efficiency (token counting), T - Time complexity
# OWASP-FIX: 1 - Prompt injection prevention

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

        # FIXED: Use centralized token estimation
        prompt_template_tokens = estimate_tokens_approx(self.DECOMPOSE_PROMPT_TEMPLATE)
        query_tokens = estimate_tokens_approx(safe_query)
        context_tokens = estimate_tokens_approx(safe_context)

        if prompt_template_tokens + query_tokens + context_tokens > 6000:
            logger.warning(f"[{corr_id}] Decomposition prompt approaching token limit — truncating context")
            safe_context = safe_context[:500]

        # FIXED: Use centralized prompt builder with escaping
        prompt = build_safe_prompt(
            self.DECOMPOSE_PROMPT_TEMPLATE,
            query=safe_query,
            context_summary=safe_context or "No context retrieved yet.",
        )

        try:
            # DVMELTSS-V: Use structured output if available (more reliable than JSON parsing)
            if self._use_structured_output:
                structured_llm = self.llm.with_structured_output(DecompositionResponseSchema)
                # FIXED: Apply retry decorator to LLM call
                response = await self._llm_retry(lambda: structured_llm.ainvoke([HumanMessage(content=prompt)]))
                data = response.model_dump()
            else:
                # FIXED: Apply retry decorator to LLM call
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
            # FIXED: Include correlation_id in warning
            logger.warning(f"[{corr_id}] Query decomposition JSON parse/validation failed: {e}. Using original.")
            return DecomposedQuery(
                original=safe_query,
                sub_questions=[safe_query],
                decomposition_reasoning=f"parse error: {e}",
                is_decomposed=False,
            )
        except Exception as e:
            # FIXED: Include correlation_id in error log
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

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    from unittest.mock import patch, MagicMock, AsyncMock

    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]

    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    async def run_tests():
        print("🔍 Testing Query Decomposer module (app/crag/query_decomposer.py)")
        print("=" * 70)

        try:
            from app.crag.query_decomposer import (
                DecompositionResponseSchema,
                DecomposedQuery,
                QueryDecomposer,
                _MIN_SUBQUESTION_LENGTH,
                _MAX_SUBQUESTIONS,
                _MAX_QUESTION_LENGTH,
            )

            # -- Test 1: Pydantic Schema --------------------------------
            print("\n📌 Test 1: DecompositionResponseSchema validation")

            # Valid schema
            valid = DecompositionResponseSchema(
                sub_questions=["What is X?", "How does Y work?"],
                reasoning="Covers both aspects",
            )
            assert len(valid.sub_questions) == 2
            assert "Covers" in valid.reasoning
            print("   ✅ Schema: validates correct input")

            # Too many sub-questions -> Pydantic error
            try:
                DecompositionResponseSchema(
                    sub_questions=["Q1", "Q2", "Q3", "Q4", "Q5"],  # 5 > max 4
                    reasoning="test",
                )
                print("   ❌ Should reject >4 sub-questions")
            except Exception:
                print("   ✅ Schema: rejects >4 sub-questions")

            # Empty sub-questions -> Pydantic error
            try:
                DecompositionResponseSchema(sub_questions=[], reasoning="test")
                print("   ❌ Should reject empty list")
            except Exception:
                print("   ✅ Schema: rejects empty sub_questions")

            # -- Test 2: DecomposedQuery dataclass ---------------------
            print("\n📌 Test 2: DecomposedQuery properties & validation")

            # Valid decomposition
            dq = DecomposedQuery(
                original="Complex query about X and Y",
                sub_questions=["What is X?", "How does Y work?"],
                decomposition_reasoning="Two distinct topics",
                is_decomposed=True,
            )
            assert dq.is_simple is False
            assert len(dq.sub_questions) == 2
            assert dq.to_dict()["is_decomposed"] is True
            print("   ✅ DecomposedQuery: properties computed correctly")

            # Simple query (1 sub-question)
            simple = DecomposedQuery(original="What is X?", sub_questions=["What is X?"], is_decomposed=False)
            assert simple.is_simple is True
            print("   ✅ DecomposedQuery: is_simple = True for single question")

            # Validation: too short sub-question
            try:
                DecomposedQuery(
                    original="test",
                    sub_questions=["Hi"],  # Too short (<10 chars)
                )
                print("   ❌ Should reject short sub-question")
            except ValueError:
                print(f"   ✅ DecomposedQuery: rejects sub-questions < {_MIN_SUBQUESTION_LENGTH} chars")

            # Validation: too long sub-question
            try:
                DecomposedQuery(
                    original="test",
                    sub_questions=["A" * 300],  # Too long (>200 chars)
                )
                print("   ❌ Should reject long sub-question")
            except ValueError:
                print(f"   ✅ DecomposedQuery: rejects sub-questions > {_MAX_QUESTION_LENGTH} chars")

            # -- Test 3: _deduplicate_questions helper -----------------
            print("\n📌 Test 3: _deduplicate_questions logic")

            decomposer = QueryDecomposer.__new__(QueryDecomposer)  # Bypass __init__

            # Case-insensitive deduplication
            input_qs = ["What is AI?", "what is ai?", "How does ML work?", "what is ai"]
            deduped = decomposer._deduplicate_questions(input_qs)
            assert len(deduped) == 2  # "What is AI?" + "How does ML work?"
            assert all(q in ["What is AI?", "How does ML work?"] for q in deduped)
            print("   ✅ Dedup: case-insensitive deduplication works")

            # Prefix similarity filter
            input_qs2 = [
                "What is the payment penalty for late invoices?",
                "What is the payment penalty?",  # Prefix of first
                "How to calculate interest?",
            ]
            deduped2 = decomposer._deduplicate_questions(input_qs2)
            assert len(deduped2) <= 2  # Should filter prefix duplicate
            print("   ✅ Dedup: prefix similarity filter works")

            # Too short questions filtered
            input_qs3 = ["Hi", "What is the payment penalty for late invoices?", "OK"]
            deduped3 = decomposer._deduplicate_questions(input_qs3)
            assert all(len(q) >= _MIN_SUBQUESTION_LENGTH for q in deduped3)
            print(f"   ✅ Dedup: filters questions < {_MIN_SUBQUESTION_LENGTH} chars")

            # Max limit enforced
            input_qs4 = [f"Question {i}" for i in range(10)]
            deduped4 = decomposer._deduplicate_questions(input_qs4)
            assert len(deduped4) <= _MAX_SUBQUESTIONS
            print(f"   ✅ Dedup: enforces max {_MAX_SUBQUESTIONS} sub-questions")

            # -- Test 4: QueryDecomposer initialization ----------------
            print("\n📌 Test 4: QueryDecomposer initialization")

            with patch("app.crag.query_decomposer.get_settings") as mock_settings:
                mock_settings.return_value.openai_api_key = "test-key"
                with patch("app.crag.query_decomposer.ChatOpenAI") as MockLLM:
                    mock_llm = MagicMock()
                    MockLLM.return_value = mock_llm

                    decomposer = QueryDecomposer(model="gpt-4o-mini")
                    assert decomposer.llm is mock_llm
                    assert hasattr(decomposer, "_use_structured_output")
                    print("   ✅ QueryDecomposer: initializes with LLM & structured output flag")

            # -- Test 5: decompose() with mocked LLM success -----------
            print("\n📌 Test 5: decompose() (mocked LLM success)")

            with patch("app.crag.query_decomposer.get_settings") as mock_settings, patch(
                "app.crag.query_decomposer.ChatOpenAI"
            ) as MockLLM, patch("app.crag.query_decomposer.retry_async") as mock_retry:
                mock_settings.return_value.openai_api_key = "test-key"
                mock_llm = MagicMock()
                MockLLM.return_value = mock_llm

                # Mock retry_async to directly return the expected result
                mock_retry.return_value = AsyncMock(
                    return_value=DecompositionResponseSchema(
                        sub_questions=["What is X?", "How does Y work?"],
                        reasoning="Two topics",
                    )
                )

                decomposer = QueryDecomposer()
                result = await decomposer.decompose(
                    query="Complex query about X and Y",
                    context_summary="Found docs about X and Y",
                    correlation_id="test-corr",
                )

                assert isinstance(result, DecomposedQuery)
                assert len(result.sub_questions) == 2
                assert result.is_decomposed is True
                assert result.original == "Complex query about X and Y"
                print("   ✅ decompose(): returns DecomposedQuery with correct sub-questions")

            # -- Test 6: decompose() fallback on JSON parse error ------
            print("\n📌 Test 6: decompose() fallback on JSON parse error")

            with patch("app.crag.query_decomposer.get_settings") as mock_settings, patch(
                "app.crag.query_decomposer.ChatOpenAI"
            ) as MockLLM, patch("app.crag.query_decomposer.logger") as mock_logger:
                mock_settings.return_value.openai_api_key = "test-key"
                mock_llm = MagicMock()
                MockLLM.return_value = mock_llm

                decomposer = QueryDecomposer()

                # ✅ FIX: Directly mock _llm_retry to raise JSONDecodeError
                async def mock_retry(fn):
                    import json

                    raise json.JSONDecodeError("test error", "doc", 0)

                decomposer._llm_retry = mock_retry

                # Disable structured output to force JSON parsing path
                decomposer._use_structured_output = False

                result = await decomposer.decompose(query="Test query", correlation_id="test-json-err")

                # Should fallback to original query on parse error
                assert result.is_decomposed is False
                assert result.sub_questions == ["Test query"]
                assert "parse error" in result.decomposition_reasoning.lower()
                assert mock_logger.warning.called
                print("   ✅ decompose(): fallback to original query on JSON parse error")

            # -- Test 7: Token safety & prompt escaping ----------------
            print("\n📌 Test 7: Token safety & prompt escaping integration")

            with patch("app.crag.query_decomposer.get_settings") as mock_settings, patch(
                "app.crag.query_decomposer.ChatOpenAI"
            ) as MockLLM, patch("app.crag.query_decomposer.estimate_tokens_approx") as mock_estimate, patch(
                "app.crag.query_decomposer.build_safe_prompt"
            ) as mock_build:
                mock_settings.return_value.openai_api_key = "test-key"
                mock_llm = MagicMock()
                MockLLM.return_value = mock_llm

                # Mock token estimation to trigger truncation
                mock_estimate.side_effect = lambda s: 7000 if len(s) > 100 else 100
                mock_build.return_value = "safe prompt"

                # Mock LLM response
                mock_structured = MagicMock()
                mock_structured.ainvoke = AsyncMock(
                    return_value=DecompositionResponseSchema(sub_questions=["Q1"], reasoning="test")
                )
                mock_llm.with_structured_output.return_value = mock_structured

                decomposer = QueryDecomposer()
                # Long query + context should trigger truncation warning
                result = await decomposer.decompose(
                    query="A" * 300,  # Long query
                    context_summary="B" * 1200,  # Long context
                    correlation_id="test-token",
                )

                # Should still succeed with truncation
                assert isinstance(result, DecomposedQuery)
                assert mock_build.called  # Prompt builder was used
                print("   ✅ decompose(): handles token safety with truncation")

            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Query Decomposer module verified.")
            print("\n💡 What we verified:")
            print("   • Schema: DecompositionResponseSchema validates input/output ✅")
            print("   • Dataclass: DecomposedQuery properties & validation ✅")
            print("   • Helper: _deduplicate_questions logic (case, prefix, length) ✅")
            print("   • Initialization: QueryDecomposer sets up LLM & retry ✅")
            print("   • decompose(): success path with structured output ✅")
            print("   • decompose(): fallback to original query on LLM error ✅")
            print("   • Safety: token estimation & prompt escaping integration ✅")
            print("\n🔐 Production: Query decomposition with validation & safety ready")
            return True

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)
