# backend/app/extraction/form_extractor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, S - Security, A - Async
# BATMAN-FIX: A - True async, T - Exponential backoff
# OWASP-FIX: 1 - Prompt escaping, 7 - Safe field handling
# ✅ FIXED: Safe sync wrapper (no deadlock in FastAPI)
# ✅ FIXED: Retry logic moved to class method for testability
# ✅ FIXED: Input validation + safe field_dict building + prompt escaping

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Final, Optional, Any

from pydantic import BaseModel, ValidationError, Field

# DVMELTSS-M: Import centralized utilities
from app.core.vision_llm import get_vision_llm
from app.core.retry import retry_async, RetryConfig
from app.core.prompts import escape_prompt_content
from app.core.openai_errors import classify_openai_error

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & CONFIG (DVMELTSS-S, BATMAN-A) -------------------------
# ========================================================================

_VALID_FORM_TYPES: Final = frozenset(
    {
        "invoice",
        "application",
        "medical_form",
        "tax_form",
        "contract_header",
        "receipt",
        "purchase_order",
        "other",
    }
)

# BATMAN-A: Token safety limits
_MAX_PROMPT_TOKENS: Final = 6000
_MAX_TEXT_LENGTH: Final = 3000
_MAX_FIELDS: Final = 100  # ✅ NEW: Limit extracted fields for embedding safety

# DVMELTSS-E: Retry configuration
_MAX_RETRIES: Final = 3
_RETRY_BASE_DELAY: Final = 1.0
_RETRY_MAX_DELAY: Final = 30.0


# DVMELTSS-V: Pydantic schemas for structured output
class FormFieldSchema(BaseModel):
    field: str = Field(..., min_length=1, max_length=100)
    value: str = Field(..., max_length=500)
    confidence: float = Field(..., ge=0.0, le=1.0)


class FormExtractionSchema(BaseModel):
    form_type: str = Field(..., pattern=f"^({'|'.join(_VALID_FORM_TYPES)})$")
    fields: list[FormFieldSchema] = Field(..., max_length=100)
    summary: str = Field(..., max_length=200)

    model_config = {"extra": "forbid"}  # ✅ FIXED: Pydantic v2 config


# ========================================================================
# -- IMMUTABLE DATA MODEL (DVMELTSS-M, V) -------------------------------
# ========================================================================


@dataclass
class ExtractedForm:
    """
    Structured form field extraction result.
    ✅ FIXED: Proper field defaults + validation in __post_init__.
    """

    form_id: str
    source_file: str
    page_number: int
    chunk_id: str

    form_type: str
    fields: list[dict] = field(default_factory=list)
    summary: str = ""
    correlation_id: Optional[str] = None

    # Built from fields for fast lookup
    field_dict: dict = field(init=False, default_factory=dict)

    def __post_init__(self):
        # ✅ Validate form_type against allowed values
        if self.form_type not in _VALID_FORM_TYPES:
            object.__setattr__(self, "form_type", "other")
        # ✅ Build lookup dict with validation
        safe_fields = []
        for f in self.fields:
            if isinstance(f, dict) and f.get("field") and f.get("value"):
                field_name = str(f["field"]).strip().lower()
                field_value = str(f["value"]).strip()
                if field_name and field_value:
                    safe_fields.append({"field": field_name, "value": field_value})
                    self.field_dict[field_name] = field_value
        # ✅ Clamp to max fields
        if len(safe_fields) > _MAX_FIELDS:
            safe_fields = safe_fields[:_MAX_FIELDS]
        object.__setattr__(self, "fields", safe_fields)

    def get_field(self, field_name: str) -> Optional[str]:
        """Case-insensitive field lookup."""
        return self.field_dict.get(field_name.lower())

    def to_embed_text(self) -> str:
        """Text for vector embedding — field:value pairs."""
        lines = [f"Form type: {self.form_type}", f"Summary: {self.summary}"]
        for f in self.fields:
            name = f.get("field", "")
            value = f.get("value", "")
            if name and value:
                lines.append(f"{name}: {value}")
        return "\n".join(lines)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "form_id": self.form_id,
            "block_type": "form",
            "form_type": self.form_type,
            "field_count": len(self.fields),
            "correlation_id": self.correlation_id,
        }

    def to_dict(self) -> dict[str, Any]:
        """✅ NEW: Convert to dict for API serialization."""
        return {
            "form_id": self.form_id,
            "source_file": self.source_file,
            "page_number": self.page_number,
            "chunk_id": self.chunk_id,
            "form_type": self.form_type,
            "fields": self.fields,
            "summary": self.summary,
            "field_dict": self.field_dict,
            "correlation_id": self.correlation_id,
        }


# ========================================================================
# -- PROMPT TEMPLATE (OWASP-1: Structured, safe) -----------------------
# ========================================================================

FORM_EXTRACTION_PROMPT = """Extract all form fields and their values from this text.
Return ONLY valid JSON matching this schema:
{{
  "form_type": "invoice|application|medical_form|tax_form|contract_header|other",
  "fields": [
    {{"field": "Invoice Number", "value": "INV-2024-001", "confidence": 0.95}},
    {{"field": "Date", "value": "2024-01-15", "confidence": 0.99}}
  ],
  "summary": "one sentence describing what this form/document header captures"
}}

Rules:
- Extract ALL field-value pairs visible in the text
- confidence: how certain you are of the extraction (0.0-1.0)
- Include dates, amounts, names, IDs, addresses, codes
- field names should be human-readable (not abbreviated)
- If value spans multiple lines, join them
"""


# ========================================================================
# -- EXTRACTOR CLASS (DVMELTSS-V, BATMAN-A, OWASP-1) -------------------
# ========================================================================


class FormExtractor:
    """
    Extracts structured key-value pairs from form documents.

    Features:
    - Centralized vision LLM client via app.core.vision_llm
    - Expanded heuristic filtering for better form detection
    - Centralized retry decorator for rate limits
    - Correlation ID tracing for audit trails
    - Async-safe interface for FastAPI integration
    """

    # FIXED: Expanded heuristic keywords for form detection
    _FORM_KEYWORDS: Final = frozenset(
        {
            "invoice",
            "application",
            "form",
            "receipt",
            "order",
            "contract",
            "name:",
            "date:",
            "amount:",
            "total:",
            "signature",
            "address",
            "phone:",
            "email:",
            "id:",
            "no:",
            "#",
            "reference",
            "bill to",
            "ship to",
            "terms:",
            "due:",
            "paid:",
            "balance:",
            "subtotal:",
            "tax:",
            "discount:",
        }
    )

    def __init__(self, model: str = "gpt-4o", max_retries: int = _MAX_RETRIES):
        # FIXED: Use centralized vision LLM pool
        self.client = get_vision_llm(model_override=model, timeout=30.0)
        self.model = model
        self.max_retries = max_retries

        logger.info(f"FormExtractor initialized: model={model}, async=True")

    # ✅ NEW: Input validation helper
    def _validate_form_text(self, text: str, corr_id: str) -> tuple[bool, str]:
        """Validate form text before processing."""
        if not text:
            return False, "text is empty"
        if not isinstance(text, str):
            return False, f"text must be str, got {type(text).__name__}"
        if len(text.strip()) < 30:
            return False, "text too short to be a form"
        if len(text) > _MAX_TEXT_LENGTH * 2:
            logger.warning(f"[{corr_id}] Text too large ({len(text)} chars) — truncating to {_MAX_TEXT_LENGTH}")
        return True, ""

    # ✅ FIXED: Moved retry logic to dedicated method for testability
    @retry_async(
        config=RetryConfig(
            max_attempts=_MAX_RETRIES,
            backoff_base=_RETRY_BASE_DELAY,
            backoff_max=_RETRY_MAX_DELAY,
            exceptions=(Exception,),
        )
    )
    async def _call_vision_api(self, prompt: str, corr_id: str):
        """Call vision LLM with retry logic."""
        # ✅ FIXED: Run sync OpenAI call in thread to avoid blocking event loop
        if sys.version_info >= (3, 9):
            return await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=800,
                response_format={"type": "json_object"},
                extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
            )
        else:
            # Python 3.8 fallback
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            return await loop.run_in_executor(
                None,
                lambda: self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=800,
                    response_format={"type": "json_object"},
                    extra_headers={"X-Correlation-ID": corr_id} if corr_id else {},
                ),
            )

    def _estimate_tokens(self, text: str) -> int:
        """BATMAN-A: Rough token estimation for prompt safety."""
        return len(text) // 4

    def _is_form_like(self, text: str) -> bool:
        """
        DVMELTSS-V: Expanded heuristic to detect form-like text.
        More robust than simple colon counting.
        """
        text_lower = text.lower()

        # Check for form keywords
        if any(kw in text_lower for kw in self._FORM_KEYWORDS):
            return True

        # Check for field patterns (Label: Value)
        field_pattern = re.compile(r"^[A-Za-z\s]+:\s*.+$", re.MULTILINE)
        if field_pattern.search(text):
            return True

        # Check for table-like structure (pipes or tabs)
        if "|" in text or "\t" in text:
            return True

        # Check for common form layouts (key-value pairs on separate lines)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) >= 3:
            # Count lines that look like field: value
            field_lines = sum(1 for l in lines if ":" in l and len(l.split(":")) >= 2)
            if field_lines >= 2:
                return True

        return False

    async def _call_llm_with_retry(
        self,
        prompt: str,
        correlation_id: Optional[str] = None,
    ) -> Optional[dict]:
        """DVMELTSS-E: Async LLM call with centralized retry + structured validation."""
        corr_id = correlation_id or "form_unknown"

        # FIXED: Use centralized prompt escaping
        safe_prompt = escape_prompt_content(prompt)

        # Token safety
        if self._estimate_tokens(safe_prompt) > _MAX_PROMPT_TOKENS:
            safe_prompt = safe_prompt[: _MAX_PROMPT_TOKENS * 4]

        try:
            response = await self._call_vision_api(safe_prompt, corr_id)
            content = response.choices[0].message.content
            if not content:
                return None

            data = json.loads(content)
            # DVMELTSS-V: Validate via Pydantic
            FormExtractionSchema.model_validate(data)
            return data

        except (ValidationError, json.JSONDecodeError) as e:
            logger.warning(f"[{corr_id}] Form extraction JSON/validation error: {e}")
            return None
        except Exception as e:
            err = classify_openai_error(e)
            if err and err.error_type == "quota":
                logger.warning(f"[{corr_id}] Form extraction: quota exceeded")
                return None
            logger.warning(f"[{corr_id}] Form extraction unexpected error: {type(e).__name__}: {e}")
            return None

    async def extract_from_text_async(
        self,
        text: str,
        form_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedForm]:
        """
        Async version: extract form fields from text content.
        BATMAN-A: Non-blocking, yields to event loop.
        ✅ FIXED: Input validation + safe field conversion + prompt escaping.
        """
        corr_id = correlation_id or "form_unknown"

        # ✅ Validate inputs first
        is_valid, error = self._validate_form_text(text, corr_id)
        if not is_valid:
            logger.debug(f"[{corr_id}] Invalid form text: {error}")
            return None

        # DVMELTSS-V: Expanded form detection
        if not self._is_form_like(text):
            logger.debug(f"[{corr_id}] Text doesn't appear to be a form")
            return None

        # FIXED: Use centralized prompt escaping + truncate text
        safe_text = escape_prompt_content(text[:_MAX_TEXT_LENGTH])
        prompt = f"{FORM_EXTRACTION_PROMPT}\n\nText:\n{safe_text}"

        try:
            data = await self._call_llm_with_retry(prompt, corr_id)
            if not data:
                return None

            fields_raw = data.get("fields", [])
            # ✅ FIXED: Slice BEFORE model_dump to limit to 50 fields
            if fields_raw and hasattr(fields_raw[0], "model_dump"):
                fields = [f.model_dump() for f in fields_raw[:_MAX_FIELDS]]
            else:
                # Fallback: ensure dict format
                fields = [
                    {
                        "field": str(f.get("field", "")),
                        "value": str(f.get("value", "")),
                        "confidence": float(f.get("confidence", 0.9)),
                    }
                    for f in fields_raw[:_MAX_FIELDS]
                    if isinstance(f, dict) and f.get("field") and f.get("value")
                ]

            if not fields:
                return None

            return ExtractedForm(
                form_id=form_id,
                source_file=source_file,
                page_number=page_number,
                chunk_id=chunk_id,
                form_type=data.get("form_type", "other"),
                fields=fields,
                summary=data.get("summary", ""),
                correlation_id=corr_id,
            )
        except Exception as e:
            logger.warning(f"[{corr_id}] Form extraction failed: {type(e).__name__}: {e}")
            return None

    def extract_from_text(
        self,
        text: str,
        form_id: str,
        source_file: str,
        page_number: int,
        chunk_id: str = "",
        correlation_id: Optional[str] = None,
    ) -> Optional[ExtractedForm]:
        """
        Sync wrapper — use extract_from_text_async() in new async code.
        ✅ FIXED: Safe event loop handling to avoid deadlocks in FastAPI.
        """
        try:
            # Check if we're already in a running event loop
            loop = asyncio.get_running_loop()
            # If yes, we can't use asyncio.run() — warn and return None
            logger.warning(
                "⚠️ FormExtractor.extract_from_text() called from async context — "
                "use extract_from_text_async() instead. Returning None."
            )
            return None
        except RuntimeError:
            # No running loop — safe to use asyncio.run()
            return asyncio.run(
                self.extract_from_text_async(text, form_id, source_file, page_number, chunk_id, correlation_id)
            )


# DVMELTSS-M: Explicit module exports
__all__ = ["FormExtractor", "ExtractedForm"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
