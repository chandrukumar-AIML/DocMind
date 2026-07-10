
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Optional

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

# DVMELTSS-M: Import centralized utilities
from app.core.llm_pool import get_llm
from app.core.prompts import (
    escape_prompt_content,
    estimate_tokens_approx,
    build_grading_prompt,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-A) -------------------------
# ========================================================================

# BATMAN-A: Use centralized prompt utilities instead of duplicate constants
_MAX_DOC_CONTENT_CHARS: Final = 400  # Per doc in grading prompt

# Retry configuration for transient LLM errors
_GRADING_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=2,
    backoff_base=0.5,
    exceptions=(Exception,),
)


# DVMELTSS-V: Pydantic schema for structured LLM output
class GradeItemSchema(BaseModel):
    doc_index: int
    label: str  # Will be validated against GradeLabel
    score: float
    reason: str
    missing_info: Optional[str] = ""


class GradingResponseSchema(BaseModel):
    grades: list[GradeItemSchema]


class GradeLabel(str, Enum):
    RELEVANT = "relevant"
    IRRELEVANT = "irrelevant"
    AMBIGUOUS = "ambiguous"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {v.value for v in cls}


@dataclass(frozen=True)
class DocumentGrade:
    """Immutable grade result for a single retrieved document."""

    document: Document
    label: GradeLabel
    score: float  # 0.0–1.0 relevance score
    reason: str
    missing_info: str = ""
    chunk_id: str = ""

    @property
    def is_relevant(self) -> bool:
        return self.label == GradeLabel.RELEVANT

    @property
    def is_irrelevant(self) -> bool:
        return self.label == GradeLabel.IRRELEVANT

    @property
    def is_ambiguous(self) -> bool:
        return self.label == GradeLabel.AMBIGUOUS


@dataclass
class GradingResult:
    """Aggregate grading result for all retrieved documents."""

    grades: list[DocumentGrade]
    query: str

    # Computed aggregates (set in __post_init__)
    relevant_count: int = field(init=False, default=0)
    irrelevant_count: int = field(init=False, default=0)
    ambiguous_count: int = field(init=False, default=0)
    mean_score: float = field(init=False, default=0.0)
    crag_action: str = field(init=False, default="generate")

    def __post_init__(self):
        # DVMELTSS-M: Compute aggregates once after initialization
        if not self.grades:
            self.crag_action = "rewrite"  # ✅ FIX: Explicitly set action for empty lists
            return  # Now it's safe to return

        self.relevant_count = sum(1 for g in self.grades if g.is_relevant)
        self.irrelevant_count = sum(1 for g in self.grades if g.is_irrelevant)
        self.ambiguous_count = sum(1 for g in self.grades if g.is_ambiguous)
        self.mean_score = sum(g.score for g in self.grades) / len(self.grades)
        self.crag_action = self._determine_action()

    def _determine_action(self) -> str:
        """DVMELTSS-M: Pure function for CRAG routing decision."""
        total = len(self.grades)
        if total == 0:
            return "rewrite"

        relevant_ratio = self.relevant_count / total

        if relevant_ratio >= 0.6:
            return "generate"
        elif relevant_ratio > 0 and self.ambiguous_count > 0:
            return "decompose"
        elif relevant_ratio > 0:
            return "filter_and_supplement"
        else:
            return "rewrite"

    @property
    def relevant_docs(self) -> list[Document]:
        return [g.document for g in self.grades if g.is_relevant]

    @property
    def missing_info_summary(self) -> str:
        """Aggregates missing_info from all grades for query rewriter."""
        missing = [g.missing_info for g in self.grades if g.missing_info]
        return "; ".join(missing[:3]) if missing else ""

    def to_dict(self) -> dict:
        return {
            "total": len(self.grades),
            "relevant": self.relevant_count,
            "irrelevant": self.irrelevant_count,
            "ambiguous": self.ambiguous_count,
            "mean_score": round(self.mean_score, 3),
            "crag_action": self.crag_action,
            "missing_info": self.missing_info_summary,
        }


class DocumentGrader:
    """
    Grades retrieved documents for relevance to a query.

    Features (DVMELTSS-V, BATMAN-A):
    - Batch grading: grade up to 5 docs in a single LLM call
    - Structured JSON output via Pydantic — reliable parsing
    - Token counting via centralized utils to prevent context window overflow
    - Prompt escaping via centralized utils to prevent injection
    - Retry logic for transient LLM errors
    - Correlation ID support for distributed tracing
    """

    GRADING_PROMPT_TEMPLATE = """You are a document relevance grader for a RAG system.
Grade each document for its relevance to the query.

Query: {query}

Documents to grade:
{documents}

Return ONLY valid JSON matching this schema:
{{
  "grades": [
    {{
      "doc_index": 0,
      "label": "relevant|irrelevant|ambiguous",
      "score": 0.85,
      "reason": "contains exact information about payment penalties",
      "missing_info": "does not specify the penalty percentage"
    }}
  ]
}}

Label definitions:
- relevant:   directly addresses the query, contains useful information
- irrelevant: completely off-topic, cannot help answer the query
- ambiguous:  partially relevant but unclear, needs more context

score: 0.0 (completely irrelevant) to 1.0 (perfectly answers query)
missing_info: what specific information is absent (helps rewrite query)
"""

    def __init__(self, model: str = "gpt-4o"):
        self.llm = get_llm(streaming=False, model_override=model, temperature_override=0.0)
        # Pre-check if structured output is supported
        self._use_structured_output = hasattr(self.llm, "with_structured_output")

        # DVMELTSS-E: Retry decorator for LLM calls
        self._llm_retry = retry_async(config=_GRADING_RETRY_CONFIG)

    async def grade_documents(
        self,
        query: str,
        documents: list[Document],
        batch_size: int = 5,
        correlation_id: Optional[str] = None,
    ) -> GradingResult:
        """
        Grade all retrieved documents for relevance.

        Args:
            query: user query to grade against
            documents: retrieved documents to evaluate
            batch_size: documents per LLM call (default 5)
            correlation_id: Request ID for distributed tracing

        Returns:
            GradingResult with per-doc grades and CRAG routing decision
        """
        corr_id = correlation_id or "unknown"

        if not documents:
            logger.debug(f"[{corr_id}] No documents to grade")
            return GradingResult(grades=[], query=query)

        all_grades: list[DocumentGrade] = []

        # Process in batches for efficiency + context window safety
        for batch_start in range(0, len(documents), batch_size):
            batch = documents[batch_start : batch_start + batch_size]
            try:
                batch_grades = await self._grade_batch(query, batch, batch_start, corr_id)
                all_grades.extend(batch_grades)
            except Exception as e:
                logger.error(f"[{corr_id}] Batch grading failed for docs {batch_start}-{batch_start+len(batch)}: {e}")
                # Fallback: mark ungraded docs as ambiguous
                for doc in batch:
                    all_grades.append(
                        DocumentGrade(
                            document=doc,
                            label=GradeLabel.AMBIGUOUS,
                            score=0.5,
                            reason=f"grading error: {e}",
                            chunk_id=doc.metadata.get("chunk_id", ""),
                        )
                    )

        result = GradingResult(grades=all_grades, query=query)
        logger.info(
            f"[{corr_id}] DocumentGrader: {result.relevant_count}/{len(documents)} relevant | "
            f"action={result.crag_action} | mean_score={result.mean_score:.3f}"
        )
        return result

    async def _grade_batch(
        self,
        query: str,
        batch: list[Document],
        offset: int = 0,
        correlation_id: Optional[str] = None,
    ) -> list[DocumentGrade]:
        """Grade a batch of documents in a single LLM call with token safety."""
        corr_id = correlation_id or "unknown"

        # Build document snippets with token-aware truncation using centralized utils
        doc_snippets = []
        for i, doc in enumerate(batch):
            content = doc.page_content[:_MAX_DOC_CONTENT_CHARS]
            snippet = (
                f"[Doc {offset + i}] (source: {doc.metadata.get('source_file','?')}, "
                f"page {doc.metadata.get('page_number', 0)+1}):\n"
                f"{escape_prompt_content(content)}"  # FIXED: Use centralized escape
            )
            doc_snippets.append(snippet)
            if estimate_tokens_approx("\n\n".join(doc_snippets) + self.GRADING_PROMPT_TEMPLATE) > 6500:
                logger.warning(
                    f"[{corr_id}] Batch prompt approaching token limit — truncating to {len(doc_snippets)} docs"
                )
                break

        prompt = build_grading_prompt(
            query=query,
            documents=doc_snippets,
            template=self.GRADING_PROMPT_TEMPLATE,
        )

        try:
            # DVMELTSS-V: Use structured output if available (more reliable than JSON parsing)
            if self._use_structured_output:
                structured_llm = self.llm.with_structured_output(GradingResponseSchema)
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
                GradingResponseSchema.model_validate(data)

        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning(f"[{corr_id}] Grade batch JSON parse/validation failed: {e}. Marking all ambiguous.")
            return [
                DocumentGrade(
                    document=doc,
                    label=GradeLabel.AMBIGUOUS,
                    score=0.5,
                    reason="parse/validation error — defaulting to ambiguous",
                    chunk_id=doc.metadata.get("chunk_id", ""),
                )
                for doc in batch
            ]
        except Exception as e:
            logger.error(f"[{corr_id}] Grade batch LLM call failed: {e}")
            return [
                DocumentGrade(
                    document=doc,
                    label=GradeLabel.RELEVANT,  # Conservative fallback: assume relevant
                    score=0.6,
                    reason=f"grading unavailable: {e}",
                    chunk_id=doc.metadata.get("chunk_id", ""),
                )
                for doc in batch
            ]

        # Parse grades with validation
        grades = []
        for item in data.get("grades", []):
            idx = item.get("doc_index", 0) - offset
            if not (0 <= idx < len(batch)):
                continue

            label_str = item.get("label", "ambiguous").lower()
            # DVMELTSS-V: Validate label against enum
            label = GradeLabel(label_str) if GradeLabel.is_valid(label_str) else GradeLabel.AMBIGUOUS

            grades.append(
                DocumentGrade(
                    document=batch[idx],
                    label=label,
                    score=float(item.get("score", 0.5)),
                    reason=str(item.get("reason", "")),
                    missing_info=str(item.get("missing_info", "")),
                    chunk_id=batch[idx].metadata.get("chunk_id", ""),
                )
            )

        # Fill any missing grades with ambiguous
        graded_indices = {id(g.document) for g in grades}
        for doc in batch:
            if id(doc) not in graded_indices:
                grades.append(
                    DocumentGrade(
                        document=doc,
                        label=GradeLabel.AMBIGUOUS,
                        score=0.5,
                        reason="not graded by LLM",
                        chunk_id=doc.metadata.get("chunk_id", ""),
                    )
                )

        return grades


# DVMELTSS-M: Explicit module exports
__all__ = ["DocumentGrader", "GradingResult", "DocumentGrade", "GradeLabel"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.crag.document_grader) -
# ========================================================================

