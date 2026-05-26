# backend/app/provenance/highlight.py
# DVMELTSS-FIX: V - Validate, M - Modular, S - Security
# ASCALE-FIX: S - Separation
# ✅ FIXED: Boundary checks + consistent length handling + input validation

from __future__ import annotations

import re
from typing import Final, Optional, Any

# DVMELTSS-S: Constants for highlight computation
_MAX_SEARCH_LEN: Final = 5000
_SHORT_NEEDLE_LEN: Final = 100
_MIN_MATCH_LEN: Final = 20
_NORMALIZED_CHUNK_LEN: Final = 200
_MAX_MAP_ITERATIONS: Final = 100000  # ✅ NEW: Prevent infinite loop on huge texts

# Color thresholds for confidence-based highlighting
_CONFIDENCE_HIGH: Final = 0.85
_CONFIDENCE_MEDIUM: Final = 0.60

# ✅ NEW: Import enum for type-safe color validation
from .models import HighlightColor


# ✅ NEW: Input validation helper
def _validate_text_inputs(page_text: Optional[str], chunk_text: Optional[str], corr_id: str) -> tuple[bool, str]:
    """Validate text inputs before processing."""
    if page_text is None or not isinstance(page_text, str):
        return False, "page_text must be a non-empty string"
    if chunk_text is None or not isinstance(chunk_text, str):
        return False, "chunk_text must be a non-empty string"
    if not page_text.strip() or not chunk_text.strip():
        return False, "page_text and chunk_text must not be empty"
    return True, ""


def compute_highlight_color(confidence_score: float) -> str:
    """
    Map confidence score to highlight color for the PDF viewer.

    Color coding:
    - green:  score >= 0.85 -> high confidence, well-supported citation
    - yellow: score >= 0.60 -> medium confidence, plausible citation
    - red:    score <  0.60 -> low confidence, verify manually
    """
    # ✅ FIXED: Clamp confidence to [0.0, 1.0] for safety
    confidence = max(0.0, min(1.0, confidence_score))
    
    if confidence >= _CONFIDENCE_HIGH:
        return HighlightColor.GREEN.value
    elif confidence >= _CONFIDENCE_MEDIUM:
        return HighlightColor.YELLOW.value
    else:
        return HighlightColor.RED.value


def find_text_offset(
    page_text: str,
    chunk_text: str,
    max_search_len: int = _MAX_SEARCH_LEN,
) -> tuple[int, int] | tuple[None, None]:
    """
    Find the character offset of chunk_text within page_text.

    Returns (start, end) character offsets in the ORIGINAL page_text,
    or (None, None) if not found.

    Strategy:
    1. Try exact match first (fastest)
    2. Try first 100 chars of chunk (handles truncated chunks)
    3. Try normalized match — but map back to original offsets
    4. Give up -> (None, None)

    Args:
        page_text:   full text content of the page
        chunk_text:  citation chunk text to locate
        max_search_len: only search within this many chars (performance)

    Returns:
        (start_offset, end_offset) in original page_text, or (None, None)
    """
    # ✅ Validate inputs
    is_valid, error = _validate_text_inputs(page_text, chunk_text, "find_offset")
    if not is_valid:
        return None, None

    search_in = page_text[:max_search_len]
    needle = chunk_text.strip()

    # Strategy 1: exact match
    idx = search_in.find(needle)
    if idx != -1:
        return idx, idx + len(needle)

    # Strategy 2: first 100 characters of chunk
    short_needle = needle[:_SHORT_NEEDLE_LEN].strip()
    if len(short_needle) >= _MIN_MATCH_LEN:
        idx = search_in.find(short_needle)
        if idx != -1:
            # ✅ FIXED: Use consistent length for end calculation
            end = min(idx + len(needle), len(search_in))
            return idx, end

    # Strategy 3: normalized match — MUST map back to original offsets
    # ✅ FIXED: Use same length for normalization as for offset calculation
    normalized_len = min(_NORMALIZED_CHUNK_LEN, len(needle))
    normalized_page = re.sub(r"\s+", " ", search_in)
    normalized_chunk = re.sub(r"\s+", " ", needle[:normalized_len]).strip()
    
    if len(normalized_chunk) >= _MIN_MATCH_LEN:
        idx_norm = normalized_page.find(normalized_chunk)
        if idx_norm != -1:
            # Map normalized offset back to original text
            orig_idx = _map_normalized_to_original(search_in, normalized_page, idx_norm)
            if orig_idx is not None:
                # ✅ FIXED: Use consistent needle length for end calculation
                end = min(orig_idx + len(needle), len(search_in))
                return orig_idx, end

    return None, None


def _map_normalized_to_original(
    original: str,
    normalized: str,
    normalized_idx: int,
) -> int | None:
    """
    Map an index from normalized text back to original text.
    Handles whitespace collapsing: "hello  world" -> "hello world"
    
    ✅ FIXED: Boundary checks + max iteration guard.
    """
    if not original or not normalized:
        return None
    
    # ✅ FIXED: Boundary check for normalized_idx
    if normalized_idx < 0 or normalized_idx > len(normalized):
        return None
    
    # Count non-whitespace chars up to normalized_idx
    target_char_count = sum(1 for c in normalized[:normalized_idx] if not c.isspace())

    # ✅ FIXED: Add max iteration guard to prevent infinite loop
    char_count = 0
    iterations = 0
    for i, c in enumerate(original):
        iterations += 1
        if iterations > _MAX_MAP_ITERATIONS:
            return None  # Give up to prevent hang
        if not c.isspace():
            char_count += 1
            if char_count > target_char_count:
                return i
    return None


def compute_citation_offsets(
    citations: list[dict],
    page_texts: dict[int, str],  # page_number -> full page text
    correlation_id: Optional[str] = None,
) -> list[dict]:
    """
    Enrich citations with character offsets for PDF highlighting.

    Args:
        citations:  list of citation dicts (from ProvenanceStore)
        page_texts: {page_number: full_text} for each referenced page
        correlation_id: Request ID for tracing (optional)

    Returns:
        citations with char_offset_start/end and highlight_color added
    """
    enriched = []
    for cit in citations:
        page_num = cit.get("page_number", 0)
        chunk_text = cit.get("chunk_text", "")
        page_text = page_texts.get(page_num, "")

        # Handle missing page text gracefully
        if not page_text or not chunk_text:
            start, end = None, None
        else:
            start, end = find_text_offset(page_text, chunk_text)

        # ✅ FIXED: Safe confidence score access with fallback
        confidence = cit.get("confidence_score") or cit.get("rerank_score", 0.0)
        confidence = float(confidence) if confidence is not None else 0.0
        
        enriched.append({
            **cit,
            "char_offset_start": start,
            "char_offset_end": end,
            "highlight_color": compute_highlight_color(confidence),
            "has_offset": start is not None,
            "correlation_id": correlation_id,
        })

    return enriched


def get_highlight_metadata() -> dict[str, Any]:
    """✅ NEW: Return highlight metadata for monitoring."""
    return {
        "confidence_thresholds": {
            "high": _CONFIDENCE_HIGH,
            "medium": _CONFIDENCE_MEDIUM,
        },
        "search_limits": {
            "max_search_len": _MAX_SEARCH_LEN,
            "short_needle_len": _SHORT_NEEDLE_LEN,
            "min_match_len": _MIN_MATCH_LEN,
            "normalized_chunk_len": _NORMALIZED_CHUNK_LEN,
            "max_map_iterations": _MAX_MAP_ITERATIONS,
        },
        "available_colors": [c.value for c in HighlightColor],
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "compute_highlight_color",
    "find_text_offset",
    "compute_citation_offsets",
    "get_highlight_metadata",
    "HighlightColor",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

