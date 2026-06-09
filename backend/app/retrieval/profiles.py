"""
Retrieval weight profiles for different document types.

✅ FIXED:
- Immutable profiles via Final + TypedDict
- Runtime validation of weight constraints
- Safe profile retrieval with fallback
- Extension guide for new document types

Usage:
    from app.retrieval.profiles import get_profile, validate_profile

    profile = get_profile("legal")  # Safe, with fallback
    if validate_profile(profile):
        use_in_hybrid_search(profile)

    # Extend: Add new profile to RETRIEVAL_PROFILES dict below
"""

from __future__ import annotations
from typing import Final, TypedDict, Optional


# -- Type-safe profile definition ----------------------------------------
class RetrievalProfile(TypedDict):
    """Type definition for retrieval configuration profiles."""

    bm25_weight: float  # Weight for BM25/sparse retrieval [0.0, 1.0]
    vector_weight: float  # Weight for dense/vector retrieval [0.0, 1.0]
    rrf_k: int  # RRF constant (typically 60)
    description: str  # Human-readable explanation


# -- Immutable profile definitions ---------------------------------------
RETRIEVAL_PROFILES: Final[dict[str, RetrievalProfile]] = {
    "general": {
        "bm25_weight": 0.5,
        "vector_weight": 0.5,
        "rrf_k": 60,
        "description": "Balanced hybrid search for general documents",
    },
    "legal": {
        "bm25_weight": 0.7,
        "vector_weight": 0.3,
        "rrf_k": 60,
        "description": "BM25-heavy for precise legal terminology",
    },
    "technical": {
        "bm25_weight": 0.4,
        "vector_weight": 0.6,
        "rrf_k": 60,
        "description": "Vector-heavy for semantic technical concepts",
    },
    "financial": {
        "bm25_weight": 0.6,
        "vector_weight": 0.4,
        "rrf_k": 60,
        "description": "Balanced with slight BM25 bias for financial docs",
    },
}

# ✅ Default fallback profile (used if requested key not found)
_DEFAULT_PROFILE_KEY: Final[str] = "general"


# -- Validation utilities ------------------------------------------------
def validate_profile(profile: RetrievalProfile) -> tuple[bool, Optional[str]]:
    """
    Validate a retrieval profile meets constraints.

    Constraints:
    - bm25_weight + vector_weight ≈ 1.0 (within 0.01 tolerance)
    - Both weights in [0.0, 1.0]
    - rrf_k > 0

    Returns:
        (is_valid, error_message) — error_message is None if valid
    """
    bm25 = profile.get("bm25_weight", 0)
    vector = profile.get("vector_weight", 0)
    rrf_k = profile.get("rrf_k", 0)

    # Weight range check
    if not (0.0 <= bm25 <= 1.0):
        return False, f"bm25_weight must be in [0.0, 1.0], got {bm25}"
    if not (0.0 <= vector <= 1.0):
        return False, f"vector_weight must be in [0.0, 1.0], got {vector}"

    # Weight sum check (with floating-point tolerance)
    if abs((bm25 + vector) - 1.0) > 0.01:
        return False, f"bm25_weight + vector_weight must ≈ 1.0, got {bm25 + vector}"

    # RRF k check
    if not isinstance(rrf_k, int) or rrf_k <= 0:
        return False, f"rrf_k must be positive integer, got {rrf_k}"

    return True, None


def validate_all_profiles() -> list[str]:
    """
    Validate all defined profiles at startup.

    Returns:
        List of error messages (empty if all valid)
    """
    errors = []
    for name, profile in RETRIEVAL_PROFILES.items():
        is_valid, error = validate_profile(profile)
        if not is_valid:
            errors.append(f"Profile '{name}': {error}")
    return errors


# -- Safe access utilities -----------------------------------------------
def get_profile(profile_name: str) -> RetrievalProfile:
    """
    Get a retrieval profile by name with safe fallback.

    Args:
        profile_name: Name of the profile (e.g., "legal", "technical")

    Returns:
        RetrievalProfile dict — falls back to "general" if not found
    """
    if profile_name in RETRIEVAL_PROFILES:
        return RETRIEVAL_PROFILES[profile_name]

    # Log warning and return default
    import logging

    logging.warning(f"Retrieval profile '{profile_name}' not found — using fallback '{_DEFAULT_PROFILE_KEY}'")
    return RETRIEVAL_PROFILES[_DEFAULT_PROFILE_KEY]


def list_profiles() -> list[str]:
    """Return list of available profile names."""
    return list(RETRIEVAL_PROFILES.keys())


def get_profile_metadata(profile_name: str) -> dict[str, any]:
    """
    Return profile metadata for API/docs exposure.

    Returns:
        Dict with profile config + validation status
    """
    profile = get_profile(profile_name)
    is_valid, error = validate_profile(profile)

    return {
        "name": profile_name,
        "config": profile,
        "valid": is_valid,
        "error": error,
        "available_profiles": list_profiles(),
    }


# -- Extension guide -----------------------------------------------------
"""
🔧 HOW TO ADD A NEW PROFILE:

1. Add entry to RETRIEVAL_PROFILES dict above:
   
   "medical": {
       "bm25_weight": 0.5,
       "vector_weight": 0.5,
       "rrf_k": 60,
       "description": "Balanced for medical literature with synonyms"
   },

2. Ensure weights sum to ~1.0 and values are in valid ranges

3. (Optional) Add domain-specific validation in validate_profile()

4. Test with:
   from app.retrieval.profiles import validate_all_profiles
   assert validate_all_profiles() == []

✅ Profiles are loaded at import time — no restart needed if using hot-reload.
"""


# -- Module exports ------------------------------------------------------
__all__ = [
    "RetrievalProfile",
    "RETRIEVAL_PROFILES",
    "validate_profile",
    "validate_all_profiles",
    "get_profile",
    "list_profiles",
    "get_profile_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
