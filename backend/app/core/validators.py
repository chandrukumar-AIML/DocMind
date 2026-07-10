
from __future__ import annotations

import re
import uuid
from typing import Final

# DVMELTSS-S: Immutable regex patterns
EMAIL_REGEX: Final = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
SLUG_REGEX: Final = re.compile(r"^[a-z][a-z0-9_-]*[a-z0-9]$")

# Password requirements
_MIN_PASSWORD_LENGTH: Final = 8
# rejected valid strong passwords containing #, ^, (, ), _, -, +, =, etc.
# New pattern: any printable non-alphanumeric ASCII char counts as "special"
_PASSWORD_COMPLEXITY_REGEX: Final = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d])[A-Za-z\d\S]{8,}$")


def validate_email(email: str, field_name: str = "email") -> str:
    """Normalize and validate email address."""
    if not email or not isinstance(email, str):
        raise ValueError(f"{field_name} must be a non-empty string")

    normalized = email.lower().strip()
    if not EMAIL_REGEX.match(normalized):
        raise ValueError(f"Invalid {field_name} format: {email}")
    return normalized


def validate_slug(slug: str, min_len: int = 3, max_len: int = 64, field_name: str = "slug") -> str:
    """Validate slug format for workspaces, users, etc."""
    if not slug or not isinstance(slug, str):
        raise ValueError(f"{field_name} must be a non-empty string")

    slug = slug.lower().strip()
    if not (min_len <= len(slug) <= max_len):
        raise ValueError(f"{field_name} must be {min_len}-{max_len} characters")
    if not SLUG_REGEX.match(slug):
        raise ValueError(f"{field_name} must match pattern: {SLUG_REGEX.pattern}")
    return slug


def validate_workspace_id(workspace_id: str) -> str:
    """
    Validate workspace ID — accepts UUIDs, "default", and slug format.

    [OK] FIXED: Original version only accepted slugs, but workspace IDs throughout
    the codebase are either UUIDs (e.g., "550e8400-e29b-41d4-a716-446655440000")
    or the literal string "default". This caused silent auth failures.
    """
    if not workspace_id or not isinstance(workspace_id, str):
        raise ValueError("workspace_id must be a non-empty string")

    workspace_id = workspace_id.strip()
    if len(workspace_id) > 64:
        raise ValueError("workspace_id too long")

    # Accept UUID format (with or without hyphens)
    try:
        uuid.UUID(workspace_id)
        return workspace_id
    except ValueError:
        pass

    # Accept literal "default"
    if workspace_id == "default":
        return workspace_id

    # Accept slug format
    if SLUG_REGEX.match(workspace_id):
        return workspace_id

    raise ValueError(
        f"Invalid workspace_id format: '{workspace_id}'. "
        "Must be a UUID, 'default', or slug (lowercase letters/digits/hyphens/underscores, 3-64 chars)"
    )


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    Validate password meets security requirements.

    Returns:
        (is_valid, error_message) — error_message specifies which requirement failed
    """
    if len(password) < _MIN_PASSWORD_LENGTH:
        return False, f"Password must be at least {_MIN_PASSWORD_LENGTH} characters"

    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    # matching the expanded _PASSWORD_COMPLEXITY_REGEX. Passwords with #, ^, (, ), +, =
    # etc. are perfectly strong but were previously rejected.
    if not re.search(r"[^A-Za-z\d]", password):
        return (
            False,
            "Password must contain at least one special character (e.g. @$!%*?&#^)",
        )

    return True, ""


def sanitize_for_display(value: str, max_len: int = 100) -> str:
    """
    Sanitize string for safe display (prevent XSS in logs/UI).

    [OK] FIXED: Uses bleach library if available for comprehensive sanitization,
    else falls back to enhanced HTML escaping.
    """
    if not value or not isinstance(value, str):
        return ""

    # Try bleach for comprehensive sanitization (install: pip install bleach)
    try:
        import bleach

        # Strip all tags, keep only text content
        cleaned = bleach.clean(value, tags=[], strip=True)
        return cleaned[:max_len]
    except ImportError:
        # Fallback: enhanced HTML escaping + strip dangerous patterns
        escaped = (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
            .replace("/", "&#x2F;")
        )
        # Remove javascript: and data: URLs
        escaped = re.sub(r"(?i)javascript\s*:", "", escaped)
        escaped = re.sub(r"(?i)data\s*:", "", escaped)
        return escaped[:max_len]


def validate_tags(tags: list[str]) -> list[str]:
    """Validate and normalize a list of tags."""
    if not isinstance(tags, list):
        raise ValueError("tags must be a list")

    normalized = []
    for tag in tags:
        if not isinstance(tag, str):
            raise ValueError("each tag must be a string")

        tag = tag.strip().lower()
        if not tag:
            continue

        if len(tag) > 50:
            raise ValueError(f"tag too long (max 50 chars): {tag}")

        if not re.match(r"^[a-z0-9_\-]+$", tag):
            raise ValueError(f"tag contains invalid characters: {tag}")

        normalized.append(tag)

    return normalized


def normalize_tags(tags: list[str]) -> list[str]:
    """
    Alias for validate_tags() with deduplication.
    Normalizes tags to lowercase, stripped, and unique (preserving order).
    """
    validated = validate_tags(tags)
    # Dedupe while preserving order (Python 3.7+ dict maintains insertion order)
    return list(dict.fromkeys(validated))


# DVMELTSS-M: Explicit module exports
__all__ = [
    "validate_email",
    "validate_slug",
    "validate_workspace_id",  # [OK] Now accepts UUIDs + "default" + slugs
    "validate_password_strength",  # [OK] Returns specific error message
    "validate_tags",
    "normalize_tags",
    "sanitize_for_display",  # [OK] Uses bleach if available
    "EMAIL_REGEX",
    "SLUG_REGEX",
]
# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.core.validators) -----
# ========================================================================

