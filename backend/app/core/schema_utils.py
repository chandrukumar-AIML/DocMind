# backend/app/core/schema_utils.py
# DVMELTSS-FIX: M - Modular, V - Validate, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
"""
Shared Pydantic utilities for DocuMind AI request/response models.

Centralizes:
- Common field validators and sanitizers
- Safe JSON schema generation
- Correlation ID handling
- Type-safe conversion helpers

Usage:
    from app.core.schema_utils import sanitize_text, validate_correlation_id
"""
from __future__ import annotations

import re
from typing import Any, Final, Optional, TypeVar, Union

from pydantic import Field, field_validator

# DVMELTSS-S: Common validation patterns
_CORRELATION_ID_PATTERN: Final = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")
_TAG_PATTERN: Final = re.compile(r"^[a-zA-Z0-9_.\-]+$")
_LANGUAGE_CODES: Final = frozenset({"en", "zh", "hi", "ar", "fr", "de", "ja", "ko", "es", "pt", "ru"})

# DVMELTSS-V: Common field constraints
_MAX_QUESTION_LENGTH: Final = 2000
_MIN_QUESTION_LENGTH: Final = 3
_MAX_TAG_LENGTH: Final = 50
_MAX_TAGS_COUNT: Final = 10
_MAX_CHAT_HISTORY: Final = 20

T = TypeVar("T")


def sanitize_text(text: str, max_length: int, min_length: int = 1, strip: bool = True) -> str:
    """
    Sanitize and validate text input.
    
    Args:
        text: Raw input string
        max_length: Maximum allowed length
        min_length: Minimum required length (default: 1)
        strip: Whether to strip whitespace (default: True)
    
    Returns:
        Sanitized text string
    
    Raises:
        ValueError: If text doesn't meet constraints
    """
    if strip:
        text = text.strip()
    
    # Collapse multiple whitespace to single space
    text = re.sub(r"\s+", " ", text)
    
    if len(text) < min_length:
        raise ValueError(f"Text must be at least {min_length} characters")
    if len(text) > max_length:
        raise ValueError(f"Text exceeds maximum length of {max_length}")
    
    return text


def validate_correlation_id(cid: Optional[str]) -> Optional[str]:
    """
    Validate correlation ID format for distributed tracing.
    
    Args:
        cid: Correlation ID string
    
    Returns:
        Validated correlation ID or None
    
    Raises:
        ValueError: If format is invalid
    """
    if cid is None:
        return None
    if not _CORRELATION_ID_PATTERN.match(cid):
        raise ValueError(
            "correlation_id must be 8-100 chars, containing only letters, numbers, hyphens, underscores"
        )
    return cid


def validate_tags(tags: list[str]) -> list[str]:
    """
    Validate and sanitize list of tags.
    
    Args:
        tags: List of tag strings
    
    Returns:
        Validated list of tags
    
    Raises:
        ValueError: If any tag is invalid
    """
    if len(tags) > _MAX_TAGS_COUNT:
        raise ValueError(f"Maximum {_MAX_TAGS_COUNT} tags allowed")
    
    validated = []
    for i, tag in enumerate(tags):
        if len(tag) > _MAX_TAG_LENGTH:
            raise ValueError(f"Tag {i} exceeds {_MAX_TAG_LENGTH} character limit")
        if not _TAG_PATTERN.match(tag):
            raise ValueError(
                f"Tag '{tag}' contains invalid characters. Use only letters, numbers, hyphens, underscores, periods."
            )
        validated.append(tag)
    
    return validated


def validate_language_code(lang: Optional[str]) -> Optional[str]:
    """
    Validate ISO language code.
    
    Args:
        lang: Language code string
    
    Returns:
        Validated language code or None
    
    Raises:
        ValueError: If code is not in allowed set
    """
    if lang is None:
        return None
    if lang not in _LANGUAGE_CODES:
        raise ValueError(f"Language must be one of: {sorted(_LANGUAGE_CODES)}")
    return lang


def validate_page_range(pages: Optional[tuple[int, int]]) -> Optional[tuple[int, int]]:
    """
    Validate page range tuple.
    
    Args:
        pages: Tuple of (start, end) page numbers
    
    Returns:
        Validated page range or None
    
    Raises:
        ValueError: If range is invalid
    """
    if pages is None:
        return None
    start, end = pages
    if start < 0 or end < 0:
        raise ValueError("Page range values must be non-negative")
    if start > end:
        raise ValueError(f"Page range start ({start}) must be <= end ({end})")
    return (start, end)


# DVMELTSS-V: Reusable Pydantic field definitions
CorrelationIdField = Field(
    default=None,
    max_length=100,
    description="Request ID for distributed tracing",
)

QuestionField = Field(
    ...,
    min_length=_MIN_QUESTION_LENGTH,
    max_length=_MAX_QUESTION_LENGTH,
    description="Natural language question",
)

TagsField = Field(
    default_factory=list,
    max_length=_MAX_TAGS_COUNT,
    description="Custom tags for categorization",
)

LanguageField = Field(
    default=None,
    max_length=5,
    description="Hint for language detection",
)


def dataclass_to_pydantic(dc: Any, model_cls: type[T], exclude: set[str] = None) -> T:
    """
    Convert internal dataclass to Pydantic response model.
    
    Args:
        dc: Source dataclass instance
        model_cls: Target Pydantic model class
        exclude: Set of field names to exclude
    
    Returns:
        Pydantic model instance
    """
    exclude = exclude or set()
    data = {
        k: v for k, v in dc.__dict__.items()
        if k not in exclude and not k.startswith("_")
    }
    return model_cls(**data)


# DVMELTSS-M: Explicit module exports
__all__ = [
    "sanitize_text",
    "validate_correlation_id",
    "validate_tags",
    "validate_language_code",
    "validate_page_range",
    "CorrelationIdField",
    "QuestionField",
    "TagsField",
    "LanguageField",
    "dataclass_to_pydantic",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

