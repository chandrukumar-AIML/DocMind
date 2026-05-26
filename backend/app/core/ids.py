# backend/app/core/ids.py
# DVMELTSS-FIX: M - Modular, S - Security, V - Validate
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 9 - Safe ID generation
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
        source_file, str(page_number), str(chunk_index), content[:100],
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

if __name__ == "__main__":
    import asyncio
    import sys
    from pathlib import Path
    
    # [FIX] ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]
    
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    
    def run_tests():
        print("[>>] Testing ID Utils module (app/core/ids.py)")
        print("=" * 70)
        
        try:
            from app.core.ids import (
                generate_deterministic_id, generate_chunk_id,
                generate_web_result_id, generate_query_id,
                generate_correlation_id, validate_id_format,
                _CHUNK_ID_PREFIX, _WEB_ID_PREFIX, _QUERY_ID_PREFIX
            )
            
            # -- Test 1: generate_deterministic_id -----------------------
            print("\n[PIN] Test 1: generate_deterministic_id")
            
            # Same inputs -> same output
            id1 = generate_deterministic_id("part1", "part2", prefix="test_")
            id2 = generate_deterministic_id("part1", "part2", prefix="test_")
            assert id1 == id2, "Same inputs should produce same ID"
            assert id1.startswith("test_")
            print(f"   [OK] Deterministic: same inputs -> same ID '{id1[:40]}...'")
            
            # Different inputs -> different output
            id3 = generate_deterministic_id("part1", "part3", prefix="test_")
            assert id1 != id3, "Different inputs should produce different IDs"
            print(f"   [OK] Unique: different inputs -> different ID")
            
            # Salt adds uniqueness
            id4 = generate_deterministic_id("part1", "part2", salt="salt1")
            id5 = generate_deterministic_id("part1", "part2", salt="salt2")
            assert id4 != id5, "Different salts should produce different IDs"
            print(f"   [OK] Salt: different salts -> different IDs")
            
            # Length parameter
            short_id = generate_deterministic_id("test", length=16)
            assert len(short_id) == 16, f"Expected length 16, got {len(short_id)}"
            print(f"   [OK] Length: custom length works (16 chars)")
            
            # -- Test 2: generate_chunk_id ------------------------------
            print("\n[PIN] Test 2: generate_chunk_id")
            
            chunk_id1 = generate_chunk_id("doc.pdf", 0, "Sample content", chunk_index=0)
            chunk_id2 = generate_chunk_id("doc.pdf", 0, "Sample content", chunk_index=0)
            assert chunk_id1 == chunk_id2, "Same chunk params -> same ID"
            assert chunk_id1.startswith(_CHUNK_ID_PREFIX)
            print(f"   [OK] Chunk ID: deterministic '{chunk_id1[:40]}...'")
            
            # Different page -> different ID
            chunk_id3 = generate_chunk_id("doc.pdf", 1, "Sample content", chunk_index=0)
            assert chunk_id1 != chunk_id3, "Different page -> different ID"
            print(f"   [OK] Chunk ID: different page -> different ID")
            
            # -- Test 3: generate_web_result_id -------------------------
            print("\n[PIN] Test 3: generate_web_result_id")
            
            web_id1 = generate_web_result_id("https://example.com", "search query")
            web_id2 = generate_web_result_id("https://example.com", "search query")
            assert web_id1 == web_id2, "Same URL+query -> same ID"
            assert web_id1.startswith(_WEB_ID_PREFIX)
            print(f"   [OK] Web result ID: deterministic '{web_id1[:40]}...'")
            
            # -- Test 4: generate_query_id ------------------------------
            print("\n[PIN] Test 4: generate_query_id (unique with random suffix)")
            
            # Same query + workspace -> different IDs (random suffix)
            query_id1 = generate_query_id("What is AI?", "ws-123")
            query_id2 = generate_query_id("What is AI?", "ws-123")
            assert query_id1 != query_id2, "Should have random suffix for uniqueness"
            assert query_id1.startswith(_QUERY_ID_PREFIX)
            assert query_id2.startswith(_QUERY_ID_PREFIX)
            print(f"   [OK] Query ID: unique with random suffix")
            print(f"      ID1: {query_id1}")
            print(f"      ID2: {query_id2}")
            
            # Different workspace -> different deterministic part
            query_id3 = generate_query_id("What is AI?", "ws-456")
            # The deterministic prefix should be different
            det1 = query_id1.rsplit('_', 1)[0]  # Remove random suffix
            det3 = query_id3.rsplit('_', 1)[0]
            assert det1 != det3, "Different workspace -> different deterministic part"
            print(f"   [OK] Query ID: different workspace -> different deterministic part")
            
            # -- Test 5: generate_correlation_id ------------------------
            print("\n[PIN] Test 5: generate_correlation_id")
            
            # Without prefix
            corr1 = generate_correlation_id()
            assert len(corr1) == 8, f"Expected 8-char UUID, got {len(corr1)}"
            print(f"   [OK] Correlation ID: 8-char UUID '{corr1}'")
            
            # With prefix
            corr2 = generate_correlation_id(prefix="api")
            assert corr2.startswith("api_"), f"Expected 'api_' prefix, got '{corr2}'"
            assert len(corr2) == 12, f"Expected 12 chars (api_ + 8), got {len(corr2)}"
            print(f"   [OK] Correlation ID: with prefix '{corr2}'")
            
            # Prefix sanitization
            corr3 = generate_correlation_id(prefix="My-API@2026!")
            assert corr3.startswith("my_api_2026_"), f"Expected sanitized prefix, got '{corr3}'"
            print(f"   [OK] Correlation ID: prefix sanitized 'My-API@2026!' -> 'my_api_2026_'")
            
            # -- Test 6: validate_id_format -----------------------------
            print("\n[PIN] Test 6: validate_id_format")
            
            # Valid IDs
            assert validate_id_format("chunk_abc123def456", prefix="chunk_", min_length=16) is True
            print(f"   [OK] Valid ID: accepted")
            
            # Too short
            assert validate_id_format("short", min_length=16) is False
            print(f"   [OK] Too short: rejected")
            
            # Wrong prefix
            assert validate_id_format("other_abc123", prefix="chunk_") is False
            print(f"   [OK] Wrong prefix: rejected")
            
            # Invalid characters
            assert validate_id_format("chunk@invalid#id") is False
            print(f"   [OK] Invalid chars: rejected")
            
            # Valid with hyphen
            assert validate_id_format("chunk_abc-123_def456", prefix="chunk_") is True
            print(f"   [OK] Hyphen allowed: accepted")
            
            print("\n" + "=" * 70)
            print("[OK] ALL TESTS PASSED! ID Utils module verified.")
            print("\n[TIP] What we verified:")
            print("   • Deterministic IDs: same inputs -> same output [OK]")
            print("   • Unique IDs: different inputs/salts -> different output [OK]")
            print("   • Chunk IDs: deterministic with source/page/index [OK]")
            print("   • Web result IDs: deterministic with URL+query [OK]")
            print("   • Query IDs: deterministic + random suffix for uniqueness [OK]")
            print("   • Correlation IDs: short UUIDs with optional sanitized prefix [OK]")
            print("   • Validation: format, length, prefix, character checks [OK]")
            print("\n[SEC] Security: Safe ID generation with collision resistance")
            return True
            
        except Exception as e:
            print(f"\n[FAIL] Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run tests (sync, no async needed)
    success = run_tests()
    sys.exit(0 if success else 1)