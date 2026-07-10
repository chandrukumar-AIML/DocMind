
"""
LLM-based change summarization for document versioning.
Centralizes prompt templates and LLM interaction logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final, Optional, Any

# DVMELTSS-M: Import centralized utilities
from app.core.llm_pool import get_llm
from app.core.retry import RetryConfig
from app.core.prompts import escape_prompt_content

logger = logging.getLogger(__name__)

# DVMELTSS-S: Summarization configuration
_SUMMARY_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    backoff_max=5.0,
    exceptions=(Exception,),
)
_MAX_SUMMARY_LENGTH: Final = 500  # chars
_LLM_TIMEOUT: Final = 30.0  # seconds

_DOMAIN_CONTEXTS: Final = {
    "legal": "legal contracts, clauses, and agreements",
    "medical": "medical records, clinical notes, and diagnoses",
    "invoice": "invoices, purchase orders, and financial documents",
    "general": "general business documents",
}


def _validate_summarizer_inputs(
    added_content: Optional[list[str]],
    removed_content: Optional[list[str]],
    modified_sections: Optional[list[dict]],
    document_type: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate summarizer inputs before processing."""
    if added_content is not None and not isinstance(added_content, list):
        return False, "added_content must be a list or None"
    if removed_content is not None and not isinstance(removed_content, list):
        return False, "removed_content must be a list or None"
    if modified_sections is not None and not isinstance(modified_sections, list):
        return False, "modified_sections must be a list or None"
    if document_type is None or not isinstance(document_type, str) or not document_type.strip():
        return False, "document_type must be a non-empty string"
    return True, ""


async def generate_change_summary_async(
    added_content: list[str],
    removed_content: list[str],
    modified_sections: list[dict],
    document_type: str,
    correlation_id: Optional[str] = None,
) -> str:
    """
    Generate concise change summary via LLM.
    Args:
        added_content: List of added text lines
        removed_content: List of removed text lines
        modified_sections: List of {old, new, position} dicts
        document_type: Domain for context-aware summarization
        correlation_id: Request ID for tracing
    Returns:
        Human-readable change summary string
    """
    corr_id = correlation_id or "summarizer_unknown"

    # ✅ Validate inputs
    is_valid, error = _validate_summarizer_inputs(
        added_content, removed_content, modified_sections, document_type, corr_id
    )
    if not is_valid:
        logger.error(f"[{corr_id}] Invalid summarizer inputs: {error}")
        return f"Error: {error}"

    domain_context = _DOMAIN_CONTEXTS.get(document_type, document_type)

    added_preview = "\n".join(added_content[:3])[:200] if added_content else "(none)"
    removed_preview = "\n".join(removed_content[:3])[:200] if removed_content else "(none)"
    modified_count = len(modified_sections or [])

    raw_prompt = f"""Summarize changes to this {domain_context} document.
ADDED (first 3 lines):
{added_preview}
REMOVED (first 3 lines):
{removed_preview}
MODIFIED sections: {modified_count}
Provide a 1-2 sentence summary focusing on:
- What substantive content changed
- Why it might matter for {document_type} documents
Keep it concise and professional."""

    try:
        prompt = escape_prompt_content(raw_prompt)
    except Exception:
        prompt = raw_prompt[:2000]  # Fallback truncation

    llm = get_llm(streaming=False, temperature_override=0.3)

    last_error = None
    for attempt in range(_SUMMARY_RETRY_CONFIG.max_attempts):
        try:
            response = await asyncio.wait_for(
                llm.ainvoke([{"role": "user", "content": prompt}]),
                timeout=_LLM_TIMEOUT,
            )

            # ✅ Safe content extraction
            if hasattr(response, "content"):
                summary = response.content.strip()
            elif isinstance(response, str):
                summary = response.strip()
            elif isinstance(response, dict) and "content" in response:
                summary = str(response["content"]).strip()
            else:
                summary = str(response).strip()

            # Truncate and clean
            if len(summary) > _MAX_SUMMARY_LENGTH:
                summary = summary[: _MAX_SUMMARY_LENGTH - 3] + "..."
            return summary

        except asyncio.TimeoutError:
            last_error = f"LLM call timed out after {_LLM_TIMEOUT}s"
            logger.warning(f"[{corr_id}] LLM summarization timed out (attempt {attempt + 1})")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"[{corr_id}] LLM summarization failed (attempt {attempt + 1}): {e}")

        if attempt < _SUMMARY_RETRY_CONFIG.max_attempts - 1:
            wait = min(
                _SUMMARY_RETRY_CONFIG.backoff_base * (2**attempt),
                _SUMMARY_RETRY_CONFIG.backoff_max,
            )
            await asyncio.sleep(wait)

    # All retries exhausted
    logger.error(f"[{corr_id}] LLM summarization failed after all retries: {last_error}")
    # Fallback summary
    return f"Document updated: {len(added_content)} lines added, {len(removed_content)} removed, {modified_count} sections modified."


def generate_fallback_summary(
    added_count: int,
    removed_count: int,
    modified_count: int,
    similarity: float,
) -> str:
    """
    Generate summary without LLM when needed.
    Args:
        added_count: Number of added lines
        removed_count: Number of removed lines
        modified_count: Number of modified sections
        similarity: Document similarity ratio (0.0-1.0)
    Returns:
        Fallback summary string
    """
    added_count = max(0, int(added_count))
    removed_count = max(0, int(removed_count))
    modified_count = max(0, int(modified_count))
    similarity = max(0.0, min(1.0, float(similarity)))

    if similarity >= 0.99:
        return "Minor formatting or whitespace changes only."

    parts = []
    if added_count > 0:
        parts.append(f"{added_count} lines added")
    if removed_count > 0:
        parts.append(f"{removed_count} lines removed")
    if modified_count > 0:
        parts.append(f"{modified_count} sections modified")

    if not parts:
        return "Document content unchanged."

    return f"Changes detected: {', '.join(parts)}. Similarity: {similarity:.1%}."


def get_summarizer_metadata() -> dict[str, Any]:
    """✅ NEW: Return summarizer metadata for debugging."""
    return {
        "retry_config": {
            "max_attempts": _SUMMARY_RETRY_CONFIG.max_attempts,
            "backoff_base": _SUMMARY_RETRY_CONFIG.backoff_base,
            "backoff_max": _SUMMARY_RETRY_CONFIG.backoff_max,
        },
        "llm_timeout_seconds": _LLM_TIMEOUT,
        "max_summary_length": _MAX_SUMMARY_LENGTH,
        "supported_domains": list(_DOMAIN_CONTEXTS.keys()),
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "generate_change_summary_async",
    "generate_fallback_summary",
    "get_summarizer_metadata",
]
# Local smoke test entry point. Run: python -m

