# backend/app/core/domain_utils.py
# DVMELTSS-FIX: M - Modular, S - Security, V - Validate
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 1 - Prompt injection prevention
"""
Shared utilities for domain-specific modules (legal, logistics, medical).

Centralizes:
- OpenAI client management via llm_pool
- Prompt building with escaping
- JSON parsing with graceful fallback
- Correlation ID handling
- Domain-specific validation helpers

Usage:
    from app.core.domain_utils import build_domain_prompt, safe_parse_llm_json
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Final, Optional, TypeVar

from app.core.prompts import build_safe_prompt, escape_prompt_content
from app.core.serializers import safe_json_loads
from app.core.llm_pool import get_llm
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: Domain-specific constants
_DOMAIN_MAX_TEXT_LENGTH: Final = 3000  # Max chars to send to LLM per chunk
_DOMAIN_MAX_TOKENS: Final = 1500  # Max response tokens for domain extraction

T = TypeVar("T")


def build_domain_prompt(template: str, **variables: str) -> str:
    """
    Build domain-specific prompt with escaped variables.
    
    Args:
        template: Prompt template with {var} placeholders
        **variables: Variables to inject (will be escaped)
    
    Returns:
        Safe prompt string ready for LLM invocation
    """
    # Truncate long text variables to prevent token overflow
    safe_vars = {}
    for k, v in variables.items():
        if isinstance(v, str) and len(v) > _DOMAIN_MAX_TEXT_LENGTH:
            safe_vars[k] = escape_prompt_content(v[:_DOMAIN_MAX_TEXT_LENGTH] + "...")
        else:
            safe_vars[k] = escape_prompt_content(v) if isinstance(v, str) else v
    return build_safe_prompt(template, **safe_vars)


def safe_parse_llm_json(content: str, default: T = None) -> T | dict | list:
    """
    Parse LLM JSON response with graceful fallback.
    
    Args:
        content: Raw LLM response content
        default: Value to return on parse failure
    
    Returns:
        Parsed JSON object or default
    """
    if not content:
        return default
    
    # Strip markdown fences if present
    cleaned = content.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        cleaned = parts[1].strip() if len(parts) >= 3 else cleaned
    
    return safe_json_loads(cleaned, default=default)


def get_domain_llm(streaming: bool = False, model_override: Optional[str] = None):
    """
    Get LLM instance configured for domain extraction tasks.
    
    Args:
        streaming: Enable token streaming (default: False for structured output)
        model_override: Optional model name override
    
    Returns:
        Configured ChatOpenAI instance
    """
    return get_llm(
        streaming=streaming,
        model_override=model_override,
        temperature_override=0.0,  # Domain extraction needs deterministic output
    )


def generate_domain_correlation_id(prefix: str = "domain") -> str:
    """Generate correlation ID for domain-specific tracing."""
    return f"{prefix}_{generate_correlation_id()}"


def validate_domain_output(data: dict, required_fields: list[str]) -> tuple[bool, str]:
    """
    Validate domain extraction output has required fields.
    
    Args:
        data: Parsed JSON output from LLM
        required_fields: List of required field names
    
    Returns:
        (is_valid, error_message)
    """
    if not isinstance(data, dict):
        return False, f"Expected dict, got {type(data).__name__}"
    
    missing = [f for f in required_fields if f not in data]
    if missing:
        return False, f"Missing required fields: {missing}"
    
    return True, ""


def validate_medical_output(data: dict) -> tuple[bool, str]:
    """Validate medical domain output structure."""
    required = ["medications"] if "medications" in data else ["diagnoses", "procedures"]
    return validate_domain_output(data, required)


def validate_legal_output(data: dict) -> tuple[bool, str]:
    """Validate legal domain output structure."""
    if "clauses" in data:
        return validate_domain_output(data, ["clauses"])
    if "obligations" in data:
        return validate_domain_output(data, ["obligations"])
    if "risk_score" in data:
        return validate_domain_output(data, ["risk_score", "risk_level"])
    return False, "Unknown legal output format"


def validate_logistics_output(data: dict) -> tuple[bool, str]:
    """Validate logistics domain output structure."""
    if "invoice_number" in data:
        return validate_domain_output(data, ["invoice_number", "total_amount"])
    if "anomalies" in data:
        return validate_domain_output(data, ["anomalies"])
    return False, "Unknown logistics output format"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "build_domain_prompt",
    "safe_parse_llm_json",
    "get_domain_llm",
    "generate_domain_correlation_id",
    "validate_domain_output",
    "validate_medical_output",
    "validate_legal_output",
    "validate_logistics_output",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

