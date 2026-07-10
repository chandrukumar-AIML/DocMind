"""
Shared ID generation utilities for DocuMind AI.

Centralizes deterministic, collision-resistant ID generation
for chunks, documents, queries, and cache keys.

Usage:
    from app.core.ids import generate_deterministic_id, generate_chunk_id
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import Final, Optional

# DVMELTSS-S: Immutable ID configuration
_CHUNK_ID_PREFIX: Final = "chunk_"
_WEB_ID_PREFIX: Final = "web_"
_QUERY_ID_PREFIX: Final = "query_"
_DEFAULT_HASH_LENGTH: Final = 32  # 32-char hex = 128-bit SHA256 prefix
_RANDOM_SUFFIX_LENGTH: Final = 8  # 8 hex chars = 32-bit randomness


def generate_deterministic_id(
    *parts: str,
    prefix: str = "",
    length: int = _DEFAULT_HASH_LENGTH,
    salt: Optional[str] = None,
) -> str:
    """
    Generate deterministic, collision-resistant ID via SHA256.

    Args:
        *parts: String components to hash together
        prefix: Optional prefix for the ID
        length: Length of hash prefix to return (default: 32)
        salt: Optional salt for additional uniqueness

    Returns:
        Deterministic ID string: {prefix}{hash[:length]}
    """
    # Combine parts with separator
    raw = "::".join(str(p) for p in parts if p)

    # Add salt if provided
    if salt:
        raw = f"{raw}::{salt}"

    # Hash and return prefixed result
    hash_hex = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{prefix}{hash_hex[:length]}"


def generate_chunk_id(
    source_file: str,
    page_number: int,
    content: str,
    chunk_index: int = 0,
    prefix: str = _CHUNK_ID_PREFIX,
) -> str:
    """
    Generate deterministic chunk ID for vector store indexing.

    Args:
        source_file: Original filename
        page_number: Page number (0-indexed)
        content: Chunk text content (use prefix for uniqueness)
        chunk_index: Index within parent chunk
        prefix: ID prefix (default: "chunk_")

    Returns:
        Deterministic chunk ID
    """
    return generate_deterministic_id(
        source_file,
        str(page_number),
        str(chunk_index),
        content[:100],
        prefix=prefix,
    )


def generate_web_result_id(url: str, query: str, prefix: str = _WEB_ID_PREFIX) -> str:
    """Generate deterministic ID for web search results."""
    return generate_deterministic_id(url, query, prefix=prefix)


def generate_query_id(query: str, workspace_id: str, timestamp: Optional[float] = None) -> str:
    """
    Generate unique query ID for tracing and caching.

    Args:
        query: User query text
        workspace_id: Workspace namespace
        timestamp: Optional timestamp for uniqueness (uses current time if None)

    Returns:
        Unique query ID with deterministic + random components
    """
    import time

    ts = timestamp or time.time()

    # Deterministic part + random suffix for uniqueness
    deterministic = generate_deterministic_id(query, workspace_id, prefix=_QUERY_ID_PREFIX)
    random_suffix = secrets.token_hex(_RANDOM_SUFFIX_LENGTH // 2)

    return f"{deterministic}_{random_suffix}"


def generate_correlation_id(prefix: Optional[str] = None) -> str:
    """
    Generate short correlation ID for distributed tracing.

    Args:
        prefix: Optional prefix for context (e.g., "api", "ocr", "agent")

    Returns:
        Format: "{prefix}_{8-char-uuid}" or just "8-char-uuid" if no prefix
    """
    uuid_short = uuid.uuid4().hex[:8]
    if prefix:
        # Sanitize prefix: lowercase, alphanumeric + underscore only, max 12 chars
        safe_prefix = "".join(c.lower() if c.isalnum() else "_" for c in prefix[:12]).rstrip("_")
        return f"{safe_prefix}_{uuid_short}"
    return uuid_short


def validate_id_format(id_value: str, prefix: Optional[str] = None, min_length: int = 16) -> bool:
    """
    Validate ID format for security and consistency.

    Args:
        id_value: ID string to validate
        prefix: Expected prefix (optional)
        min_length: Minimum total length (default: 16)

    Returns:
        True if ID format is valid
    """
    if not id_value or len(id_value) < min_length:
        return False
    if prefix and not id_value.startswith(prefix):
        return False
    # Ensure only safe characters (alphanumeric + underscore + hyphen)
    return all(c.isalnum() or c in "_-" for c in id_value)


# DVMELTSS-M: Explicit module exports
__all__ = [
    "generate_deterministic_id",
    "generate_chunk_id",
    "generate_web_result_id",
    "generate_query_id",
    "generate_correlation_id",
    "validate_id_format",
]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.ids) ------------
# ========================================================================

