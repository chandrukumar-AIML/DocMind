# backend/app/core/vectorstore_utils.py
# DVMELTSS-FIX: M - Modular, S - Security, V - Validate
# ASCALE-FIX: S - Separation, C - Coupling
# BATMAN-FIX: A - True async for I/O operations
"""
Shared utilities for vector store modules.

Centralizes:
- ChromaDB key sanitization to prevent injection
- Safe metadata coercion with detailed logging
- Correlation ID propagation for tracing
- Atomic file operations for cache/index persistence

Usage:
    from app.core.vectorstore_utils import sanitize_chroma_key, atomic_save
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Final, Optional
from app.core.ids import generate_correlation_id


logger = logging.getLogger(__name__)

# DVMELTSS-S: ChromaDB key sanitization pattern
_CHROMA_KEY_SANITIZE_PATTERN: Final = re.compile(r"[{}:*\"\\s]")
_MAX_KEY_LENGTH: Final = 255

# DVMELTSS-V: Required metadata fields (shared across stores)
REQUIRED_METADATA_FIELDS: Final = [
    "source_file",
    "page_number",
    "chunk_id",
    "parent_id",
    "block_type",
    "language",
    "ocr_confidence",
    "chunk_type",
    "ingest_timestamp",
    "document_type",
    "char_count",
]

# Allowed filter keys for similarity search
ALLOWED_FILTER_KEYS: Final = frozenset(
    {
        "source_file",
        "page_number",
        "block_type",
        "language",
        "chunk_type",
        "document_type",
        "ocr_confidence",
    }
)
ALLOWED_FILTER_OPERATORS: Final = frozenset({"$gte", "$lte", "$gt", "$lt", "$eq", "$ne", "$in", "$nin"})


def sanitize_chroma_key(key: str, prefix: str = "") -> str:
    """
    Sanitize ChromaDB key to prevent injection attacks.

    Args:
        key: Raw key string
        prefix: Optional prefix to prepend after sanitization

    Returns:
        Safe ChromaDB key string
    """
    if not key:
        raise ValueError("ChromaDB key cannot be empty")

    # Remove dangerous characters
    safe = _CHROMA_KEY_SANITIZE_PATTERN.sub("_", key)
    # Collapse multiple underscores
    safe = re.sub(r"_+", "_", safe)
    # Strip leading/trailing underscores
    safe = safe.strip("_")
    # Add prefix if provided
    if prefix:
        safe = f"{prefix}:{safe}"
    # Enforce max length
    if len(safe) > _MAX_KEY_LENGTH:
        safe = safe[:_MAX_KEY_LENGTH]

    return safe


def coerce_metadata_value(value: Any, field_name: str) -> Any:
    """
    Coerce metadata value to expected type with detailed logging.

    Args:
        value: Raw value
        field_name: Field name for error messages

    Returns:
        Coerced value or original if coercion fails
    """
    if field_name == "page_number":
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Metadata coercion failed for '{field_name}': {value} -> using 0")
            return 0
    elif field_name == "ocr_confidence":
        try:
            return float(value)
        except (ValueError, TypeError):
            logger.warning(f"Metadata coercion failed for '{field_name}': {value} -> using 0.0")
            return 0.0
    elif field_name == "char_count":
        try:
            return int(value)
        except (ValueError, TypeError):
            logger.warning(f"Metadata coercion failed for '{field_name}': {value} -> using 0")
            return 0
    # Convert non-serializable types to string
    if isinstance(value, (list, dict, set)):
        return str(value)
    return value


def validate_metadata(meta: dict, required_fields: list[str] = None) -> tuple[bool, Optional[str]]:
    """
    Validate metadata has required fields.

    Args:
        meta: Metadata dict
        required_fields: List of required field names (uses default if None)

    Returns:
        (is_valid, error_message)
    """
    fields = required_fields or REQUIRED_METADATA_FIELDS
    missing = [f for f in fields if f not in meta]
    if missing:
        return False, f"Missing required metadata fields: {missing}"
    return True, None


def validate_filter(filter_dict: dict) -> tuple[bool, Optional[str]]:
    """
    Validate filter dict against allowed keys and operators.

    Args:
        filter_dict: Filter dict to validate

    Returns:
        (is_valid, error_message)
    """
    invalid_keys = set(filter_dict.keys()) - ALLOWED_FILTER_KEYS
    if invalid_keys:
        return (
            False,
            f"Invalid filter keys: {invalid_keys}. Allowed: {ALLOWED_FILTER_KEYS}",
        )

    def validate_value(value: Any) -> tuple[bool, Optional[str]]:
        if isinstance(value, dict):
            invalid_ops = set(value.keys()) - ALLOWED_FILTER_OPERATORS
            if invalid_ops:
                return (
                    False,
                    f"Invalid filter operator: {invalid_ops}. Allowed: {ALLOWED_FILTER_OPERATORS}",
                )
            for nested in value.values():
                valid, err = validate_value(nested)
                if not valid:
                    return False, err
        return True, None

    for value in filter_dict.values():
        valid, err = validate_value(value)
        if not valid:
            return False, err

    return True, None


async def atomic_save(data: Any, path: Path, save_fn: callable) -> bool:
    """
    Save data atomically using temp file + rename pattern.

    Args:
        data: Data to save
        path: Target file path
        save_fn: Function that writes data to a file path

    Returns:
        True if save successful, False otherwise
    """
    try:
        temp_path = path.with_suffix(".tmp")
        # Run blocking I/O in thread to avoid event loop freeze
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: save_fn(data, temp_path))
        # Atomic rename
        await loop.run_in_executor(None, lambda: temp_path.replace(path))
        return True
    except OSError as e:
        if "No space left" in str(e):
            logger.error(f"Disk full — cannot save to {path}")
        else:
            logger.warning(f"Atomic save failed for {path}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error saving {path}: {e}")
        return False


def generate_vectorstore_correlation_id(prefix: str = "vectorstore") -> str:
    """Generate correlation ID for vector store operations."""
    return f"{prefix}_{generate_correlation_id()}"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "sanitize_chroma_key",
    "coerce_metadata_value",
    "validate_metadata",
    "validate_filter",
    "atomic_save",
    "generate_vectorstore_correlation_id",
    "REQUIRED_METADATA_FIELDS",
    "ALLOWED_FILTER_KEYS",
    "ALLOWED_FILTER_OPERATORS",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
