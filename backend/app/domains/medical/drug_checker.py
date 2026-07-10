
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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

DRUG_EXTRACTION_PROMPT: Final = """Extract all medications from this clinical text.

Return ONLY valid JSON:
{{
  "medications": [
    {{
      "name": "Metformin",
      "generic": "metformin hydrochloride",
      "dosage": "500mg",
      "frequency": "twice daily",
      "route": "oral"
    }}
  ]
}}

Text:
{text}
"""

INTERACTION_CHECK_PROMPT: Final = """You are a clinical pharmacologist. Check these medications
for known drug interactions.

Medications: {medications}

Return ONLY valid JSON:
{{
  "interactions": [
    {{
      "drug_1": "Warfarin",
      "drug_2": "Aspirin",
      "severity": "major|moderate|minor",
      "description": "Increased bleeding risk",
      "recommendation": "Monitor INR closely; consider alternative analgesic"
    }}
  ],
  "high_risk_count": 1,
  "moderate_risk_count": 0
}}
"""


@dataclass
class DrugInteraction:
    """A detected drug-drug interaction."""

    drug_1: str
    drug_2: str
    severity: str  # major / moderate / minor
    description: str
    recommendation: str
    correlation_id: str = ""  # FIXED: Added for tracing


@dataclass
class DrugCheckResult:
    """Result of medication extraction and interaction checking."""

    medications: list[dict]
    interactions: list[DrugInteraction] = field(default_factory=list)
    high_risk_count: int = 0
    moderate_risk_count: int = 0
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def has_major_interactions(self) -> bool:
        return self.high_risk_count > 0


class DrugInteractionChecker:
    """
    Extracts medications from clinical notes and checks for interactions.
    Uses GPT-4o with pharmacology knowledge.

    NOTE: This is a screening tool only.
    Always confirm with a licensed pharmacist for clinical decisions.
    """

    def __init__(self, model: str = "gpt-4o"):
        self.llm = get_domain_llm(streaming=False, model_override=model)
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=2,
                backoff_base=0.5,
                exceptions=(Exception,),
            )
        )

    async def check(
        self,
        chunks: list[Document],
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> DrugCheckResult:
        """Extract medications and check for interactions."""
        corr_id = correlation_id or generate_domain_correlation_id("medical")

        # Step 1: Extract all medications
        all_meds = []
        for chunk in chunks:
            meds = await self._extract_medications(chunk.page_content, corr_id)
            all_meds.extend(meds)

        seen: set[str] = set()
        unique_meds = []
        for m in all_meds:
            name = m.get("name", "").lower().strip()
            if name and name not in seen:
                seen.add(name)
                unique_meds.append(m)

        if len(unique_meds) < 2:
            return DrugCheckResult(medications=unique_meds, correlation_id=corr_id)

        # Step 2: Check interactions
        interactions, high, moderate = await self._check_interactions(unique_meds, corr_id)

        return DrugCheckResult(
            medications=unique_meds,
            interactions=interactions,
            high_risk_count=high,
            moderate_risk_count=moderate,
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )

    async def _extract_medications(self, text: str, corr_id: str) -> list[dict]:
        """Extract medications from a text chunk."""
        med_keywords = [
            "mg",
            "prescribed",
            "medication",
            "drug",
            "tablet",
            "capsule",
            "injection",
            "dose",
            "twice",
            "daily",
        ]
        if not any(kw in text.lower() for kw in med_keywords):
            return []

        prompt = build_domain_prompt(DRUG_EXTRACTION_PROMPT, text=text[:2000])
        try:
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            data = safe_parse_llm_json(response.content, default={"medications": []})

            is_valid, error = validate_medical_output(data)
            if not is_valid:
                logger.warning(f"[{corr_id}] Invalid drug extraction: {error}")
                return []

            return data.get("medications", [])
        except Exception as e:
            logger.debug(f"[{corr_id}] Drug extraction failed: {e}")
            return []

    async def _check_interactions(
        self,
        medications: list[dict],
        corr_id: str,
    ) -> tuple[list[DrugInteraction], int, int]:
        """Check a medication list for interactions."""
        med_names = [m.get("name", "") for m in medications if m.get("name")]
        prompt = build_domain_prompt(INTERACTION_CHECK_PROMPT, medications=", ".join(med_names[:20]))
        try:
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            data = safe_parse_llm_json(response.content, default={"interactions": []})

            is_valid, error = validate_medical_output(data)
            if not is_valid:
                logger.warning(f"[{corr_id}] Invalid interaction output: {error}")
                return [], 0, 0

            interactions = [
                DrugInteraction(
                    drug_1=str(item.get("drug_1", "")),
                    drug_2=str(item.get("drug_2", "")),
                    severity=str(item.get("severity", "minor")),
                    description=str(item.get("description", "")),
                    recommendation=str(item.get("recommendation", "")),
                    correlation_id=corr_id,  # FIXED: Propagate correlation_id
                )
                for item in data.get("interactions", [])
            ]
            high = int(data.get("high_risk_count", 0))
            moderate = int(data.get("moderate_risk_count", 0))
            return interactions, high, moderate
        except Exception as e:
            logger.warning(f"[{corr_id}] Interaction check failed: {e}")
            return [], 0, 0


# DVMELTSS-M: Explicit module exports
__all__ = ["DrugInteractionChecker", "DrugCheckResult", "DrugInteraction"]
# Local smoke test entry point. Run: python -m

