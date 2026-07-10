"""
Shared utilities for OCR modules.

Centralizes:
- PII scrubbing patterns for GDPR/HIPAA compliance
- Image token calculation per OpenAI Vision pricing
- Bounding box normalization
- Language detection with numpy vectorization
- Correlation ID propagation for distributed tracing

Usage:
    from app.core.ocr_utils import scrub_pii_for_ocr, calculate_vision_tokens
"""

from __future__ import annotations

import logging  # ✅ FIXED: Added missing logging import
import re
import numpy as np
from typing import Final
from app.core.ids import generate_correlation_id

# DVMELTSS-S: Immutable PII patterns — compiled once, reused everywhere
_PII_EMAIL: Final = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b")
_PII_PHONE: Final = re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_PII_SSN: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PII_CARD: Final = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
_PII_IBAN: Final = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

# VISION-FIX: OpenAI Vision token calculation constants
_VISION_LOW_DETAIL_TOKENS: Final = 85
_VISION_TILE_TOKENS: Final = 170
_VISION_MAX_DIM: Final = 2048
_VISION_MIN_DIM_FOR_TILING: Final = 768
_VISION_TILE_SIZE: Final = 512

# OCR-FIX: Language detection thresholds
_LANG_MIN_TEXT_LENGTH: Final = 10
_LANG_MIN_SCRIPT_RATIO: Final = 0.15
_LANG_MIN_SCRIPT_COUNT: Final = 3


def scrub_pii_for_ocr(text: str, domain: str = "general") -> str:
    """
    Scrub PII from text before OCR/Vision API calls.

    Args:
        text: Raw text to sanitize
        domain: Domain for additional patterns (medical/legal/logistics)

    Returns:
        Sanitized text with PII replaced by placeholders
    """
    if not text:
        return ""

    # Apply universal patterns
    text = _PII_EMAIL.sub("[EMAIL]", text)
    text = _PII_PHONE.sub("[PHONE]", text)
    text = _PII_SSN.sub("[SSN]", text)
    text = _PII_CARD.sub("[CARD]", text)

    # Domain-specific patterns
    if domain in ("medical", "all"):
        text = re.sub(
            r"\b(?:MRN|Medical\s+Record)[:\s]*[A-Z0-9\-]{5,20}\b",
            "[MRN]",
            text,
            flags=re.I,
        )

    if domain in ("legal", "all"):
        text = re.sub(
            r"\b(?:Contract\s+No|Agreement\s+No)[:\s]*[A-Z0-9\-]{5,20}\b",
            "[CONTRACT_ID]",
            text,
            flags=re.I,
        )

    return text


def calculate_vision_tokens(width: int, height: int, detail: str = "high") -> int:
    """
    Calculate token cost for an image per OpenAI Vision pricing.

    Args:
        width: Image width in pixels
        height: Image height in pixels
        detail: "low" or "high" detail mode

    Returns:
        Estimated token count for the image
    """
    if detail == "low":
        return _VISION_LOW_DETAIL_TOKENS

    # Step 1: Resize to max dimension
    if max(width, height) > _VISION_MAX_DIM:
        scale = _VISION_MAX_DIM / max(width, height)
        width = int(width * scale)
        height = int(height * scale)

    # Step 2: Scale for tiling calculation
    min_dim = min(width, height)
    if min_dim > _VISION_MIN_DIM_FOR_TILING:
        scale = _VISION_MIN_DIM_FOR_TILING / min_dim
        width = int(width * scale)
        height = int(height * scale)

    # Step 3: Count tiles (ceiling division)
    tiles_w = -(-width // _VISION_TILE_SIZE)
    tiles_h = -(-height // _VISION_TILE_SIZE)
    tiles = tiles_w * tiles_h

    return _VISION_TILE_TOKENS * tiles + _VISION_LOW_DETAIL_TOKENS


def normalize_bbox(bbox, default: list[list[float]] = None) -> list[list[float]]:
    """
    Normalize bounding box to 4-point polygon format.

    Handles: 4-point polygon, 2-point rect, flat list, or invalid input.

    Args:
        bbox: Raw bounding box in any supported format
        default: Fallback bbox if normalization fails

    Returns:
        Normalized 4-point polygon: [[x1,y1], [x2,y1], [x2,y2], [x1,y2]]
    """
    if default is None:
        default = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0]]

    try:
        # Case 1: Already 4-point polygon
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            if all(isinstance(p, (list, tuple)) and len(p) == 2 for p in bbox):
                return [list(p) for p in bbox]
            # Flat 4-value list [x1, y1, x2, y2]
            if all(isinstance(v, (int, float)) for v in bbox):
                x1, y1, x2, y2 = bbox
                return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        # Case 2: 2-point format [[x1,y1], [x2,y2]]
        if isinstance(bbox, (list, tuple)) and len(bbox) == 2:
            if all(isinstance(p, (list, tuple)) and len(p) == 2 for p in bbox):
                (x1, y1), (x2, y2) = bbox
                return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

        # Fallback
        logger = logging.getLogger(__name__)  # ✅ FIXED: logging now imported
        logger.warning(f"Unexpected bbox format: {bbox}. Using default.")
        return default

    except Exception as e:
        logger = logging.getLogger(__name__)  # ✅ FIXED: logging now imported
        logger.error(f"BBOX normalization failed: {e} | input: {bbox}")
        return default


def detect_language_vectorized(text: str, min_length: int = _LANG_MIN_TEXT_LENGTH) -> str:
    """
    Numpy-vectorized language detection with configurable thresholds.

    Args:
        text: Text to analyze
        min_length: Minimum text length for reliable detection

    Returns:
        ISO language code: "en", "zh", "hi", "ar", "ta", "te", "ml", "kn", "bn", etc.
    """
    if not text or len(text) < min_length:
        return "en"

    codes = np.frombuffer(text.encode("utf-32-le"), dtype=np.uint32)
    total = max(len(codes), 1)

    # Script-specific character ranges
    cjk = np.sum((codes >= 0x4E00) & (codes <= 0x9FFF))
    arabic = np.sum((codes >= 0x0600) & (codes <= 0x06FF))
    devanagari = np.sum((codes >= 0x0900) & (codes <= 0x097F))
    bengali = np.sum((codes >= 0x0980) & (codes <= 0x09FF))
    tamil = np.sum((codes >= 0x0B80) & (codes <= 0x0BFF))
    telugu = np.sum((codes >= 0x0C00) & (codes <= 0x0C7F))
    kannada = np.sum((codes >= 0x0C80) & (codes <= 0x0CFF))
    malayalam = np.sum((codes >= 0x0D00) & (codes <= 0x0D7F))

    # Require minimum ratio AND absolute count for classification
    if cjk / total > _LANG_MIN_SCRIPT_RATIO and cjk >= _LANG_MIN_SCRIPT_COUNT:
        return "zh"
    if arabic / total > _LANG_MIN_SCRIPT_RATIO and arabic >= _LANG_MIN_SCRIPT_COUNT:
        return "ar"
    if devanagari / total > _LANG_MIN_SCRIPT_RATIO and devanagari >= _LANG_MIN_SCRIPT_COUNT:
        return "hi"
    if tamil / total > _LANG_MIN_SCRIPT_RATIO and tamil >= _LANG_MIN_SCRIPT_COUNT:
        return "ta"
    if telugu / total > _LANG_MIN_SCRIPT_RATIO and telugu >= _LANG_MIN_SCRIPT_COUNT:
        return "te"
    if malayalam / total > _LANG_MIN_SCRIPT_RATIO and malayalam >= _LANG_MIN_SCRIPT_COUNT:
        return "ml"
    if kannada / total > _LANG_MIN_SCRIPT_RATIO and kannada >= _LANG_MIN_SCRIPT_COUNT:
        return "kn"
    if bengali / total > _LANG_MIN_SCRIPT_RATIO and bengali >= _LANG_MIN_SCRIPT_COUNT:
        return "bn"

    return "en"


def generate_ocr_correlation_id(prefix: str = "ocr") -> str:
    """Generate correlation ID for OCR operations."""
    return f"{prefix}_{generate_correlation_id()}"


# DVMELTSS-M: Reusable field definitions for Pydantic models
from pydantic import Field

BBoxField = Field(..., description="Bounding box as 4-point polygon [[x1,y1],...]", min_length=4)
ConfidenceField = Field(..., ge=0.0, le=1.0, description="Confidence score 0.0-1.0")

# DVMELTSS-M: Explicit module exports
__all__ = [
    "scrub_pii_for_ocr",
    "calculate_vision_tokens",
    "normalize_bbox",
    "detect_language_vectorized",
    "generate_ocr_correlation_id",
    "BBoxField",
    "ConfidenceField",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.ocr_utils) -------
# ========================================================================

