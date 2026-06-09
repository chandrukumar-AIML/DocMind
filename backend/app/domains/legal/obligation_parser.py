# backend/app/domains/legal/obligation_parser.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.domain_utils import (
    build_domain_prompt,
    safe_parse_llm_json,
    get_domain_llm,
    validate_legal_output,
    generate_domain_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

OBLIGATION_PROMPT: Final = """Extract all legal obligations from this contract text.

An obligation is: Party X MUST/SHALL do Y by/within Z.

Return ONLY valid JSON:
{{
  "obligations": [
    {{
      "party": "Vendor",
      "obligation": "maintain $1M errors and omissions insurance",
      "deadline": "throughout the term of this agreement",
      "consequence": "material breach entitling Client to terminate",
      "section_ref": "Section 6.3",
      "obligation_type": "insurance|payment|delivery|reporting|compliance|other"
    }}
  ]
}}

Contract text:
{text}
"""


@dataclass
class Obligation:
    """A single contractual obligation."""

    party: str
    obligation: str
    deadline: str = ""
    consequence: str = ""
    section_ref: str = ""
    obligation_type: str = "other"
    source_file: str = ""
    page_number: int = 0
    correlation_id: str = ""  # FIXED: Added for tracing


class ObligationParser:
    """Extracts structured obligations from contract text."""

    def __init__(self, model: str = "gpt-4o"):
        # FIXED: Use centralized LLM pool
        self.llm = get_domain_llm(streaming=False, model_override=model)
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=2,
                backoff_base=0.5,
                exceptions=(Exception,),
            )
        )

    async def parse(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> list[Obligation]:
        """Parse obligations from all document chunks."""
        corr_id = correlation_id or generate_domain_correlation_id("legal")
        obligations = []

        for chunk in chunks:
            text = chunk.page_content
            page_num = chunk.metadata.get("page_number", 0)

            # Quick filter: skip chunks unlikely to have obligations
            obligation_keywords = [
                "shall",
                "must",
                "required",
                "obligation",
                "responsible",
                "will",
                "agrees to",
                "undertakes",
            ]
            if not any(kw in text.lower() for kw in obligation_keywords):
                continue

            # FIXED: Use centralized prompt builder
            prompt = build_domain_prompt(OBLIGATION_PROMPT, text=text[:2500])

            try:
                # FIXED: Apply retry + centralized JSON parsing
                response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
                data = safe_parse_llm_json(response.content, default={"obligations": []})

                is_valid, error = validate_legal_output(data)
                if not is_valid:
                    logger.warning(f"[{corr_id}] Invalid obligation output: {error}")
                    continue

                for item in data.get("obligations", []):
                    obligations.append(
                        Obligation(
                            party=str(item.get("party", ""))[:50],
                            obligation=str(item.get("obligation", ""))[:300],
                            deadline=str(item.get("deadline", "")),
                            consequence=str(item.get("consequence", "")),
                            section_ref=str(item.get("section_ref", "")),
                            obligation_type=str(item.get("obligation_type", "other")),
                            source_file=source_file,
                            page_number=page_num,
                            correlation_id=corr_id,  # FIXED: Propagate correlation_id
                        )
                    )
            except Exception as e:
                logger.warning(f"[{corr_id}] Obligation parse failed: {e}")

        logger.info(f"[{corr_id}] Obligations parsed: {source_file} | {len(obligations)} found")
        return obligations


# DVMELTSS-M: Explicit module exports
__all__ = ["ObligationParser", "Obligation"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
