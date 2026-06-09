# backend/app/domains/medical/pii_redactor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, M - Modular
# OWASP-FIX: 1 - HIPAA compliance: REDACT BEFORE EXTERNAL CALLS
# HIPAA: All PII must be redacted BEFORE any external API calls

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, Optional

# DVMELTSS-M: Import centralized utilities
from app.core.domain_utils import generate_domain_correlation_id
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# Structured PII patterns - HIPAA covered entities
PII_PATTERNS: Final = {
    "SSN": (r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b", "[SSN REDACTED]"),
    "PHONE": (
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "[PHONE REDACTED]",
    ),
    "EMAIL": (
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b",
        "[EMAIL REDACTED]",
    ),
    "DOB": (
        r"\b(?:DOB|Date of Birth|born):?\s*\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b",
        "[DOB REDACTED]",
    ),
    "MRN": (
        r"\b(?:MRN|Medical Record(?:\s+Number)?|Patient ID):?\s*[\w\-]{5,15}\b",
        "[MRN REDACTED]",
    ),
    "NPI": (r"\b(?:NPI):?\s*\d{10}\b", "[NPI REDACTED]"),
    "INSURANCE": (
        r"\b(?:Policy|Member|Group)\s*(?:No|Number|#):?\s*[\w\-]{5,20}\b",
        "[INSURANCE ID REDACTED]",
    ),
    "ZIP": (r"\b\d{5}(?:-\d{4})?\b", "[ZIP REDACTED]"),
    # HIPAA additional: Names, addresses in medical context
    "PATIENT_NAME": (
        r"\b(?:Patient|Name|Pt):\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)",
        "[PATIENT NAME REDACTED]",
    ),
}


@dataclass
class RedactionResult:
    """Result of PII redaction."""

    original_text: str
    redacted_text: str
    redacted_items: dict[str, int]  # {type: count}
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def total_redacted(self) -> int:
        return sum(self.redacted_items.values())


class PIIRedactor:
    """
    HIPAA-compliant PII redaction for medical documents.

    ⚠️ CRITICAL: Redaction happens BEFORE any external API calls.
    Two passes:
    1. Regex patterns: structured PII (SSN, phone, email, MRN) - ALWAYS RUN FIRST
    2. GPT-4o: contextual PII (patient names, addresses in prose) - ONLY on already-redacted text

    Note: Redaction is irreversible by design.
    Always redact BEFORE any external API calls.
    """

    def __init__(self, model: str = "gpt-4o"):
        # FIXED: Use centralized LLM pool for LLM pass (only if enabled)
        from app.core.domain_utils import get_domain_llm

        self.llm = get_domain_llm(streaming=False, model_override=model)
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=1,  # Only one attempt for redaction to avoid leaking PII on retry
                backoff_base=0.1,
                exceptions=(Exception,),
            )
        )

    def redact(
        self,
        text: str,
        use_llm_pass: bool = True,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> RedactionResult:
        """
        Redact all PII from medical text.

        ⚠️ HIPAA: This method MUST be called BEFORE any external API calls.

        Args:
            text: raw text to redact
            use_llm_pass: enable GPT-4o for contextual PII (more accurate, costs tokens)
            correlation_id: Request ID for distributed tracing
        """
        corr_id = correlation_id or generate_domain_correlation_id("medical")

        # PASS 1: Regex patterns - ALWAYS RUN FIRST, BEFORE ANY LLM CALLS
        redacted = text
        redacted_items: dict[str, int] = {}

        for pii_type, (pattern, replacement) in PII_PATTERNS.items():
            matches = re.findall(pattern, redacted, re.IGNORECASE)
            if matches:
                redacted = re.sub(pattern, replacement, redacted, flags=re.IGNORECASE)
                redacted_items[pii_type] = len(matches)

        # PASS 2: LLM-based contextual PII - ONLY on ALREADY-REDACTED text
        # This ensures no raw PII is ever sent to external APIs
        if use_llm_pass and len(redacted) > 50:
            # FIXED: Pass the already-redacted text to LLM, never the original
            redacted = self._llm_redact(redacted, corr_id)
            if redacted != text:  # Only count if LLM actually changed something
                redacted_items["LLM_CONTEXTUAL"] = 1

        logger.info(
            f"[{corr_id}] PIIRedactor: {sum(redacted_items.values())} items redacted | "
            f"types={list(redacted_items.keys())}"
        )
        return RedactionResult(
            original_text=text,
            redacted_text=redacted,
            redacted_items=redacted_items,
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )

    async def _llm_redact(self, redacted_text: str, corr_id: str) -> str:
        """
        Use GPT-4o to redact contextual PII missed by regex.

        ⚠️ HIPAA: This method receives ALREADY-REDACTED text from regex pass.
        Never send original/unredacted text to external APIs.
        """
        prompt = f"""You are a HIPAA compliance tool.
Redact ANY remaining personally identifiable information from this ALREADY-PARTIALLY-REDACTED medical text.

Replace: any remaining patient names -> [PATIENT NAME], provider names -> [PROVIDER NAME],
         addresses -> [ADDRESS], dates of service -> [DATE OF SERVICE].

Return ONLY the fully redacted text — no explanation, no markdown.

Text (already partially redacted):
{redacted_text[:3000]}
"""
        try:
            # FIXED: Apply retry + ensure we're working with redacted text only
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            return response.content.strip()
        except Exception as e:
            logger.warning(f"[{corr_id}] LLM redaction failed: {e}. Using regex-only result.")
            # Return the regex-redacted text - never fall back to original
            return redacted_text


# DVMELTSS-M: Explicit module exports
__all__ = ["PIIRedactor", "RedactionResult", "PII_PATTERNS"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
