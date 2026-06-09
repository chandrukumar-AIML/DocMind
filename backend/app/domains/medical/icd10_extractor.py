# backend/app/domains/medical/icd10_extractor.py
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
    validate_medical_output,
    generate_domain_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

ICD10_PROMPT: Final = """You are a medical coding specialist. Extract all diagnoses and 
procedures from this clinical text and assign ICD-10 codes.

Return ONLY valid JSON:
{{
  "diagnoses": [
    {{
      "description": "Type 2 Diabetes Mellitus without complications",
      "icd10_code": "E11.9",
      "code_type": "diagnosis|procedure",
      "confidence": 0.95,
      "evidence_text": "quote from note confirming this diagnosis",
      "is_primary": true
    }}
  ],
  "procedures": [
    {{
      "description": "HbA1c measurement",
      "icd10_code": "Z13.1",
      "code_type": "procedure",
      "confidence": 0.90
    }}
  ]
}}

Clinical text:
{text}
"""


@dataclass
class ICD10Code:
    """An extracted ICD-10 diagnosis or procedure code."""

    description: str
    icd10_code: str
    code_type: str  # "diagnosis" | "procedure"
    confidence: float
    evidence_text: str = ""
    is_primary: bool = False
    source_file: str = ""
    page_number: int = 0
    correlation_id: str = ""  # FIXED: Added for tracing


class ICD10Extractor:
    """Extracts ICD-10 codes from clinical notes using GPT-4o."""

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

    async def extract(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> list[ICD10Code]:
        """Extract ICD-10 codes from clinical document chunks."""
        corr_id = correlation_id or generate_domain_correlation_id("medical")
        all_codes: list[ICD10Code] = []
        seen_codes: set[str] = set()

        for chunk in chunks:
            text = chunk.page_content
            page_num = chunk.metadata.get("page_number", 0)

            # Filter: only process clinical-looking text
            medical_keywords = [
                "diagnosis",
                "patient",
                "prescribed",
                "treatment",
                "mg",
                "symptoms",
                "history",
                "assessment",
                "plan",
            ]
            if not any(kw in text.lower() for kw in medical_keywords):
                continue

            prompt = build_domain_prompt(ICD10_PROMPT, text=text[:2500])
            try:
                response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
                data = safe_parse_llm_json(response.content, default={})

                is_valid, error = validate_medical_output(data)
                if not is_valid:
                    logger.warning(f"[{corr_id}] Invalid ICD-10 output: {error}")
                    continue

                for section in ("diagnoses", "procedures"):
                    for item in data.get(section, []):
                        code = str(item.get("icd10_code", "")).strip()
                        if not code or code in seen_codes:
                            continue
                        seen_codes.add(code)
                        all_codes.append(
                            ICD10Code(
                                description=str(item.get("description", "")),
                                icd10_code=code,
                                code_type=str(item.get("code_type", "diagnosis")),
                                confidence=float(item.get("confidence", 0.8)),
                                evidence_text=str(item.get("evidence_text", ""))[:200],
                                is_primary=bool(item.get("is_primary", False)),
                                source_file=source_file,
                                page_number=page_num,
                                correlation_id=corr_id,  # FIXED: Propagate correlation_id
                            )
                        )
            except Exception as e:
                logger.warning(f"[{corr_id}] ICD-10 extraction failed: {e}")

        logger.info(f"[{corr_id}] ICD-10: {source_file} | {len(all_codes)} codes extracted")
        return all_codes


# DVMELTSS-M: Explicit module exports
__all__ = ["ICD10Extractor", "ICD10Code"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
