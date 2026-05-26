# backend/app/core/openai_errors.py
# DVMELTSS-FIX: M - Modular, E - Error handling, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
"""
OpenAI error classification utilities.

Centralizes detection of:
- Quota/billing errors
- Authentication failures
- Rate limit vs quota distinction
- Connection/timeout categorization

Usage:
    from app.core.openai_errors import get_openai_error_type, is_insufficient_quota_error
    error_type = get_openai_error_type(exc)
"""
from __future__ import annotations

from openai import APIError, RateLimitError, AuthenticationError, APIConnectionError, APITimeoutError

# ── Global quota flag ─────────────────────────────────────────────────────────
# Set to True once any component detects quota/auth exceeded. All components
# should check this before making OpenAI calls to avoid per-request retry storms.
_openai_quota_exceeded: bool = False
_openai_auth_failed: bool = False


def mark_openai_quota_exceeded() -> None:
    global _openai_quota_exceeded
    _openai_quota_exceeded = True


def mark_openai_auth_failed() -> None:
    global _openai_auth_failed
    _openai_auth_failed = True


def is_openai_available() -> bool:
    """Returns False if quota or auth error has been detected — skip all OpenAI calls."""
    return not (_openai_quota_exceeded or _openai_auth_failed)


def is_insufficient_quota_error(exc: Exception) -> bool:
    """
    Detect OpenAI quota/billing errors from exception message or type.
    
    Checks:
    - Known error message patterns (case-insensitive)
    - HTTP 429 status code with quota-related content
    - Specific OpenAI exception types
    
    Args:
        exc: Exception instance (OpenAI or generic)
        
    Returns:
        True if exception indicates quota/billing issue
    """
    # Check exception type first (most reliable)
    if isinstance(exc, RateLimitError):
        # RateLimitError can be quota or rate limit — check message
        message = str(exc).lower()
        if any(pattern in message for pattern in [
            "insufficient_quota",
            "exceeded your current quota",
            "you exceeded your current quota",
            "plan and billing details",
            "upgrade your plan",
        ]):
            return True
    
    # Check message patterns for any exception type
    message = str(exc).lower()
    quota_patterns = [
        "insufficient_quota",
        "exceeded your current quota",
        "you exceeded your current quota",
        "plan and billing details",
        "upgrade your plan",
        "billing details",
        "credit balance",
        "add payment method",
    ]
    if any(pattern in message for pattern in quota_patterns):
        return True
    
    # Check HTTP status code if available
    if hasattr(exc, "status_code") and exc.status_code == 429:
        # 429 could be rate limit OR quota — check response body if available
        if hasattr(exc, "response") and exc.response:
            try:
                body = exc.response.text.lower() if hasattr(exc.response, "text") else ""
                if any(p in body for p in quota_patterns):
                    return True
            except Exception:
                pass  # Safely ignore response parsing errors
    
    return False


def is_authentication_error(exc: Exception) -> bool:
    """
    Detect OpenAI authentication errors.
    
    Args:
        exc: Exception instance
        
    Returns:
        True if exception indicates auth failure
    """
    if isinstance(exc, AuthenticationError):
        return True
    
    message = str(exc).lower()
    auth_patterns = [
        "incorrect api key",
        "invalid api key",
        "authentication failed",
        "unauthorized",
        "api key",
    ]
    return any(pattern in message for pattern in auth_patterns)


def is_rate_limit_error(exc: Exception) -> bool:
    """
    Detect OpenAI rate limit errors (distinct from quota errors).
    
    Args:
        exc: Exception instance
        
    Returns:
        True if exception indicates rate limit (not quota)
    """
    if isinstance(exc, RateLimitError) and not is_insufficient_quota_error(exc):
        return True
    
    message = str(exc).lower()
    # Rate limit patterns (not quota)
    rate_patterns = [
        "rate limit reached",
        "requests per minute",
        "requests per day",
        "too many requests",
    ]
    return any(pattern in message for pattern in rate_patterns) and "quota" not in message


def get_openai_error_type(exc: Exception) -> str:
    """
    Classify OpenAI error into actionable categories.
    
    Returns:
        One of: "quota", "auth", "rate_limit", "connection", "timeout", "other"
    """
    if is_insufficient_quota_error(exc):
        return "quota"
    if is_authentication_error(exc):
        return "auth"
    if is_rate_limit_error(exc):
        return "rate_limit"
    if isinstance(exc, APIConnectionError):
        return "connection"
    if isinstance(exc, APITimeoutError):
        return "timeout"
    if isinstance(exc, APIError):
        return "api_error"
    return "other"


# -- Alias for Backward Compatibility --------------------------------------

def classify_openai_error(error: Exception) -> dict:
    """
    Classify OpenAI API errors into actionable categories.
    
    This is an alias for get_openai_error_type that returns a dict
    for backward compatibility with existing code.
    
    Args:
        error: Exception instance (OpenAI or generic)
        
    Returns:
        dict with keys: category, retryable, message, code
    """
    error_type = get_openai_error_type(error)
    
    # Map error type to retryable status and message
    error_map = {
        "quota": {
            "retryable": False,
            "message": "Insufficient quota. Please check your OpenAI billing settings.",
            "code": "insufficient_quota",
        },
        "auth": {
            "retryable": False,
            "message": "Authentication failed. Please check your API key.",
            "code": "authentication_failed",
        },
        "rate_limit": {
            "retryable": True,
            "message": "Rate limit exceeded. Retrying with exponential backoff.",
            "code": "rate_limit_exceeded",
        },
        "connection": {
            "retryable": True,
            "message": "Connection error. Retrying...",
            "code": "connection_error",
        },
        "timeout": {
            "retryable": True,
            "message": "Request timeout. Retrying...",
            "code": "timeout_error",
        },
        "api_error": {
            "retryable": False,
            "message": f"OpenAI API error: {str(error)}",
            "code": "api_error",
        },
        "other": {
            "retryable": False,
            "message": f"Unknown error: {str(error)}",
            "code": "unknown_error",
        },
    }
    
    config = error_map.get(error_type, error_map["other"])
    
    return {
        "category": error_type,
        "retryable": config["retryable"],
        "message": config["message"],
        "code": config["code"],
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "is_insufficient_quota_error",
    "is_authentication_error",
    "is_rate_limit_error",
    "get_openai_error_type",
    "classify_openai_error",  # ✅ NEW: Added for backward compatibility
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

