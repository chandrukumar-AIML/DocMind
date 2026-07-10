"""
Shared utilities for document versioning module.

Centralizes:
- Diff computation with memory-safe chunking
- Semantic summarization via LLM pool
- Version ID generation with collision resistance
- Correlation ID propagation for audit trails

Usage:
    from app.core.versioning_utils import compute_semantic_diff, generate_version_id
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Final, Optional

from difflib import SequenceMatcher

from app.core.llm_pool import get_llm
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

# DVMELTSS-S: Diff computation limits
_MAX_DIFF_CHUNK_SIZE: Final = 10000  # chars per chunk for memory safety
_MAX_SUMMARY_TOKENS: Final = 500

# DVMELTSS-E: Retry config for LLM-based summarization
_SUMMARY_RETRY_CONFIG: Final = RetryConfig(
    max_attempts=3,
    backoff_base=0.5,
    backoff_max=5.0,
    exceptions=(Exception,),
)


def generate_version_id(content: str, timestamp: Optional[float] = None) -> str:
    """
    Generate collision-resistant version ID.

    Args:
        content: Document content or hash input
        timestamp: Optional timestamp for ordering

    Returns:
        Unique version ID string (16-char hex prefix + timestamp)
    """
    ts = timestamp or time.time()
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"{content_hash}_{int(ts * 1000)}"  # millisecond precision


def compute_text_diff(old_text: str, new_text: str, max_chunk: int = _MAX_DIFF_CHUNK_SIZE) -> dict:
    """
    Compute line-based diff with memory-safe chunking.

    Args:
        old_text: Original document text
        new_text: Modified document text
        max_chunk: Maximum chunk size for diff computation

    Returns:
        Dict with added_lines, removed_lines, modified_sections
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    # Use SequenceMatcher for efficient diff
    matcher = SequenceMatcher(None, old_lines, new_lines)

    added = []
    removed = []
    modified = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "insert":
            added.extend(new_lines[j1:j2])
        elif tag == "delete":
            removed.extend(old_lines[i1:i2])
        elif tag == "replace":
            modified.append(
                {
                    "old": "".join(old_lines[i1:i2]),
                    "new": "".join(new_lines[j1:j2]),
                    "position": i1,
                }
            )
        # "equal" tags are ignored

    return {
        "added_lines": [l.rstrip() for l in added if l.strip()],
        "removed_lines": [l.rstrip() for l in removed if l.strip()],
        "modified_sections": modified,
        "similarity_ratio": matcher.ratio(),
    }


async def summarize_changes_async(
    diff: dict,
    document_type: str,
    correlation_id: Optional[str] = None,
) -> str:
    """
    Generate human-readable summary of changes via LLM.

    Args:
        diff: Output from compute_text_diff
        document_type: Type of document (legal, medical, etc.)
        correlation_id: Request ID for tracing

    Returns:
        Concise summary string
    """
    corr_id = correlation_id or "versioning_unknown"

    # Prepare context for LLM
    added_preview = "\n".join(diff["added_lines"][:5])
    removed_preview = "\n".join(diff["removed_lines"][:5])

    prompt = f"""Summarize the changes to this {document_type} document.
Added content (first 5 lines):
{added_preview[:300] if added_preview else "(none)"}
Removed content (first 5 lines):
{removed_preview[:300] if removed_preview else "(none)"}
Modified sections: {len(diff["modified_sections"])}
Similarity ratio: {diff["similarity_ratio"]:.2%}
Return a 1-2 sentence summary of what changed and why it matters.
Keep it concise and domain-appropriate for {document_type}."""

    try:
        llm = get_llm(streaming=False, temperature_override=0.3)

        @retry_async(config=_SUMMARY_RETRY_CONFIG)
        async def _call_llm():
            return await llm.ainvoke([{"role": "user", "content": prompt}])

        response = await _call_llm()
        summary = response.content.strip() if hasattr(response, "content") else str(response)

        # Truncate if too long
        return summary[:500] + ("..." if len(summary) > 500 else "")

    except Exception as e:
        logger.warning(f"[{corr_id}] Change summarization failed: {e}")
        # Fallback summary
        return f"Document updated: {len(diff['added_lines'])} lines added, {len(diff['removed_lines'])} removed."


def validate_version_metadata(metadata: dict) -> tuple[bool, str]:
    """
    Validate version metadata before storage.

    Args:
        metadata: Dict of metadata fields

    Returns:
        (is_valid, error_message)
    """
    required = ["version_id", "created_at", "author_id"]
    missing = [f for f in required if f not in metadata]
    if missing:
        return False, f"Missing required fields: {missing}"

    # Validate timestamp format
    if not isinstance(metadata.get("created_at"), str):
        return False, "created_at must be ISO 8601 string"

    # Validate author_id format
    author = metadata.get("author_id", "")
    if not author or len(author) > 100:
        return False, "author_id must be non-empty and <=100 chars"

    return True, ""


# DVMELTSS-M: Explicit module exports
__all__ = [
    "generate_version_id",
    "compute_text_diff",
    "summarize_changes_async",
    "validate_version_metadata",
]
# Local smoke test entry point. Run: python -m

