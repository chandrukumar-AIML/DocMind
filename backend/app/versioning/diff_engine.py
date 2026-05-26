# backend/app/versioning/diff_engine.py
# DVMELTSS-FIX: V - Validate, E - Error handling, A - Async, M - Modular
# BATMAN-FIX: A - True async, M - Memory safety
# ASCALE-FIX: L - Layered, E - Error propagation
from __future__ import annotations
import asyncio
import logging
from typing import Final, Optional
# DVMELTSS-M: Import centralized utilities
from app.core.versioning_utils import (
    compute_text_diff,
    summarize_changes_async,
    generate_version_id,
    validate_version_metadata,
)
from app.core.schema_utils import validate_correlation_id
from .models import DiffResult, VersionComparison
logger = logging.getLogger(__name__)
# DVMELTSS-S: Diff configuration
_MIN_SIMILARITY_FOR_SKIP: Final = 0.99  # Skip versioning if docs are 99%+ similar
async def compute_document_diff(
    old_content: str,
    new_content: str,
    document_id: str,
    document_type: str = "general",
    correlation_id: Optional[str] = None,  # FIXED: Added for tracing
) -> DiffResult:
    """
    Compute semantic diff between two document versions.
    Args:
        old_content: Previous version text
        new_content: Current version text
        document_id: Unique document identifier
        document_type: Domain type for context-aware diff
        correlation_id: Request ID for distributed tracing
    Returns:
        DiffResult with structured change information
    """
    corr_id = validate_correlation_id(correlation_id) or "diff_unknown"
    try:
        # Quick similarity check to skip unnecessary processing
        if len(old_content) > 100 and len(new_content) > 100:
            # Simple ratio check before full diff
            from difflib import SequenceMatcher
            ratio = SequenceMatcher(None, old_content[:5000], new_content[:5000]).ratio()
            if ratio >= _MIN_SIMILARITY_FOR_SKIP:
                logger.debug(f"[{corr_id}] Documents {ratio:.2%} similar — skipping detailed diff")
                return DiffResult(
                    document_id=document_id,
                    has_changes=False,
                    similarity_ratio=ratio,
                    added_lines=[],
                    removed_lines=[],
                    modified_sections=[],
                    change_summary="No significant changes detected.",
                    correlation_id=corr_id,
                )
        # Compute detailed diff with memory-safe chunking
        diff = compute_text_diff(old_content, new_content)
        has_changes = bool(diff["added_lines"] or diff["removed_lines"] or diff["modified_sections"])
        # Generate summary if there are changes
        change_summary = ""
        if has_changes:
            change_summary = await summarize_changes_async(
                diff=diff,
                document_type=document_type,
                correlation_id=corr_id,
            )
        return DiffResult(
            document_id=document_id,
            has_changes=has_changes,
            similarity_ratio=diff["similarity_ratio"],
            added_lines=diff["added_lines"],
            removed_lines=diff["removed_lines"],
            modified_sections=diff["modified_sections"],
            change_summary=change_summary,
            correlation_id=corr_id,  # FIXED: Propagate correlation_id
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Diff computation failed: {e}", exc_info=True)
        # Return minimal result on failure
        return DiffResult(
            document_id=document_id,
            has_changes=True,  # Assume changes on error to be safe
            similarity_ratio=0.0,
            added_lines=[],
            removed_lines=[],
            modified_sections=[],
            change_summary=f"Error computing diff: {str(e)[:100]}",
            correlation_id=corr_id,
            error=str(e),
        )
async def summarize_changes(
    diff_result: DiffResult,
    document_type: str = "general",
    correlation_id: Optional[str] = None,
) -> str:
    """
    Generate human-readable summary from a DiffResult.
    Args:
        diff_result: Output from compute_document_diff
        document_type: Domain for context-aware summarization
        correlation_id: Request ID for tracing
    Returns:
        Concise change summary string
    """
    corr_id = validate_correlation_id(correlation_id) or diff_result.correlation_id or "summary_unknown"
    if not diff_result.has_changes:
        return "No significant changes detected."
    try:
        return await summarize_changes_async(
            diff={
                "added_lines": diff_result.added_lines,
                "removed_lines": diff_result.removed_lines,
                "modified_sections": diff_result.modified_sections,
                "similarity_ratio": diff_result.similarity_ratio,
            },
            document_type=document_type,
            correlation_id=corr_id,
        )
    except Exception as e:
        logger.warning(f"[{corr_id}] Change summarization failed: {e}")
        return f"Changes detected: {len(diff_result.added_lines)} additions, {len(diff_result.removed_lines)} removals."
def compare_versions(
    version_a: dict,
    version_b: dict,
    correlation_id: Optional[str] = None,
) -> VersionComparison:
    """
    Compare two version metadata dicts for API responses.
    Args:
        version_a: First version metadata
        version_b: Second version metadata
        correlation_id: Request ID for tracing
    Returns:
        VersionComparison with side-by-side metadata
    """
    corr_id = validate_correlation_id(correlation_id) or "compare_unknown"
    return VersionComparison(
        version_a_id=version_a.get("version_id", ""),
        version_b_id=version_b.get("version_id", ""),
        created_at_a=version_a.get("created_at", ""),
        created_at_b=version_b.get("created_at", ""),
        author_a=version_a.get("author_id", ""),
        author_b=version_b.get("author_id", ""),
        change_summary=version_b.get("change_summary", ""),
        correlation_id=corr_id,  # FIXED: Propagate correlation_id
    )
# DVMELTSS-M: Explicit module exports
__all__ = ["compute_document_diff", "summarize_changes", "compare_versions"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

