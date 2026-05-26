# backend/app/domains/legal/clause_extractor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability
# BATMAN-FIX: A - API efficiency, B - Batch processing
# OWASP-FIX: 1 - Prompt injection prevention

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Final, Optional

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.config import get_settings
from app.core.domain_utils import (
    build_domain_prompt, 
    safe_parse_llm_json, 
    get_domain_llm,
    validate_legal_output,
    generate_domain_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# Clause type taxonomy
CLAUSE_TYPES: Final = [
    "liability_cap", "indemnification", "termination", "payment_terms",
    "intellectual_property", "confidentiality", "governing_law",
    "dispute_resolution", "force_majeure", "warranty",
    "limitation_of_remedies", "assignment", "amendment",
    "entire_agreement", "other",
]

# Regex patterns to detect clause boundaries
CLAUSE_PATTERNS: Final = [
    r"(?:Section|Article|Clause|§)\s*[\d\.]+[A-Za-z]?\s*[:\.]?\s*([A-Z][^\.]{5,80})",
    r"\b(?:WHEREAS|NOW,?\s+THEREFORE|IN\s+WITNESS)\b",
    r"\b(?:\d+\.\d+)\s+([A-Z][a-z][\w\s]{5,60})\b",
]

CLAUSE_EXTRACTION_PROMPT: Final = """You are a legal contract analysis expert.
Analyze this text and extract all legal clauses.

Return ONLY valid JSON:
{{
  "clauses": [
    {{
      "clause_type": "{clause_types}",
      "title": "clause title or section heading",
      "text": "full clause text verbatim (max 500 chars)",
      "section_ref": "Section 4.2 or Article VIII etc",
      "key_terms": ["important term 1", "term 2"],
      "has_specific_values": true,
      "specific_values": ["$500,000", "30 days", "New York"]
    }}
  ]
}}

Document text:
{text}
"""


@dataclass
class ExtractedClause:
    """A single extracted legal clause with metadata."""
    clause_type: str
    title: str
    text: str
    section_ref: str = ""
    key_terms: list[str] = field(default_factory=list)
    has_specific_values: bool = False
    specific_values: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    page_number: int = 0
    source_file: str = ""


@dataclass
class ClauseExtractionResult:
    """Complete clause extraction for a document."""
    source_file: str
    clauses: list[ExtractedClause] = field(default_factory=list)
    missing_standard_clauses: list[str] = field(default_factory=list)
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def clause_count(self) -> int:
        return len(self.clauses)

    def by_type(self, clause_type: str) -> list[ExtractedClause]:
        return [c for c in self.clauses if c.clause_type == clause_type]

    def highest_risk_clauses(self, n: int = 5) -> list[ExtractedClause]:
        return sorted(self.clauses, key=lambda c: c.risk_score, reverse=True)[:n]


class ClauseExtractor:
    """
    Extracts and classifies legal clauses from contract documents.

    Two-stage process:
    1. Regex: detect likely clause boundaries + section references
    2. GPT-4o: classify type, extract full text, identify key terms
    """

    STANDARD_CLAUSES: Final = {
        "liability_cap", "indemnification", "termination",
        "payment_terms", "governing_law", "confidentiality",
    }

    def __init__(self, model: str = "gpt-4o"):
        # FIXED: Use centralized LLM pool
        self.llm = get_domain_llm(streaming=False, model_override=model)
        
        # DVMELTSS-E: Retry config for LLM calls
        self._llm_retry = retry_async(config=RetryConfig(
            max_attempts=2,
            backoff_base=0.5,
            exceptions=(Exception,),
        ))

    async def extract_from_chunks(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,
    ) -> ClauseExtractionResult:
        """
        Extract clauses from all chunks of a legal document.

        Args:
            chunks: LangChain Documents (child chunks)
            source_file: document filename
            correlation_id: Request ID for distributed tracing

        Returns:
            ClauseExtractionResult with all found clauses
        """
        corr_id = correlation_id or generate_domain_correlation_id("legal")
        result = ClauseExtractionResult(source_file=source_file, correlation_id=corr_id)

        # Process chunks in batches of 3 (balance cost vs coverage)
        batch_size = 3
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i: i + batch_size]
            batch_text = "\n\n".join(c.page_content for c in batch)
            page_num = batch[0].metadata.get("page_number", 0)

            clauses = await self._extract_from_text(batch_text, source_file, page_num, corr_id)
            result.clauses.extend(clauses)

        # Identify missing standard clauses
        found_types = {c.clause_type for c in result.clauses}
        result.missing_standard_clauses = [
            t for t in self.STANDARD_CLAUSES if t not in found_types
        ]

        logger.info(
            f"[{corr_id}] Clause extraction: {source_file} | "
            f"{result.clause_count} clauses | "
            f"missing={result.missing_standard_clauses}"
        )
        return result

    async def _extract_from_text(
        self,
        text: str,
        source_file: str,
        page_number: int,
        correlation_id: str,
    ) -> list[ExtractedClause]:
        """Extract clauses from a text block using GPT-4o."""
        if len(text.strip()) < 100:
            return []

        clause_types_str = " | ".join(CLAUSE_TYPES)
        
        # FIXED: Use centralized prompt builder with escaping
        prompt = build_domain_prompt(
            CLAUSE_EXTRACTION_PROMPT,
            clause_types=clause_types_str,
            text=text[:3000],
        )

        try:
            # FIXED: Apply retry decorator to LLM call
            response = await self._llm_retry(
                lambda: self.llm.ainvoke([{"role": "user", "content": prompt}])
            )
            
            # FIXED: Use centralized JSON parser with graceful fallback
            data = safe_parse_llm_json(response.content, default={"clauses": []})
            
            # Validate output structure
            is_valid, error = validate_legal_output(data)
            if not is_valid:
                logger.warning(f"[{correlation_id}] Invalid clause extraction output: {error}")
                return []

            clauses = []
            for item in data.get("clauses", []):
                clause_type = item.get("clause_type", "other")
                if clause_type not in CLAUSE_TYPES:
                    clause_type = "other"

                clauses.append(ExtractedClause(
                    clause_type=clause_type,
                    title=str(item.get("title", ""))[:100],
                    text=str(item.get("text", ""))[:500],
                    section_ref=str(item.get("section_ref", "")),
                    key_terms=[str(t) for t in item.get("key_terms", [])[:10]],
                    has_specific_values=bool(item.get("has_specific_values", False)),
                    specific_values=[str(v) for v in item.get("specific_values", [])[:10]],
                    page_number=page_number,
                    source_file=source_file,
                ))
            return clauses

        except Exception as e:
            # FIXED: Include correlation_id in error log
            logger.warning(f"[{correlation_id}] Clause extraction failed: {e}")
            return []


# DVMELTSS-M: Explicit module exports
__all__ = ["ClauseExtractor", "ExtractedClause", "ClauseExtractionResult", "CLAUSE_TYPES"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

