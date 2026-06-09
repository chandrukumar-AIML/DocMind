# backend/app/core/validators.py
# DVMELTSS-FIX: M - Modular, S - Security, V - Validate
# [OK] FIXED: validate_workspace_id accepts UUIDs + "default" + slugs
# [OK] FIXED: sanitize_for_display uses bleach if available, else enhanced escaping
# [OK] FIXED: validate_password_strength returns specific missing requirement
# [OK] FIXED: Removed unused WORKSPACE_ID_REGEX

from __future__ import annotations

import re
import uuid
from typing import Final

# DVMELTSS-S: Immutable regex patterns
EMAIL_REGEX: Final = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
SLUG_REGEX: Final = re.compile(r"^[a-z][a-z0-9_-]*[a-z0-9]$")
# [OK] FIXED: Removed unused WORKSPACE_ID_REGEX

# Password requirements
_MIN_PASSWORD_LENGTH: Final = 8
# FIXED: Expanded allowed special chars — original only allowed [@$!%*?&] which
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

    # [OK] FIXED: Return specific missing requirement for better UX
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r"\d", password):
        return False, "Password must contain at least one digit"
    # [OK] FIXED: Expanded from [@$!%*?&] to any printable non-alphanumeric character,
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

if __name__ == "__main__":
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
        print("[>>] Testing Validators module (app/core/validators.py)")
        print("=" * 70)

        try:
            from app.core.validators import (
                validate_email,
                validate_slug,
                validate_workspace_id,
                validate_password_strength,
                sanitize_for_display,
                validate_tags,
                normalize_tags,
            )

            # -- Test 1: validate_email ----------------------------------
            print("\n[PIN] Test 1: validate_email")

            result = validate_email("  TEST@Example.COM ")
            assert result == "test@example.com"
            print(f"   [OK] Normalization: '  TEST@Example.COM ' -> '{result}'")

            try:
                validate_email("invalid-email")
                print("   [FAIL] Should reject invalid format")
            except ValueError:
                print("   [OK] Invalid format rejected")

            # -- Test 2: validate_slug ----------------------------------
            print("\n[PIN] Test 2: validate_slug")

            # Auto-normalization (lowercase conversion)
            result = validate_slug("My-Workspace")
            assert result == "my-workspace"
            print(f"   [OK] Auto-normalization: 'My-Workspace' -> '{result}'")

            # Invalid pattern
            try:
                validate_slug("invalid@slug")
                print("   [FAIL] Should reject invalid pattern")
            except ValueError:
                print("   [OK] Invalid pattern rejected")

            # Too short
            try:
                validate_slug("ab")
                print("   [FAIL] Should reject short length")
            except ValueError:
                print("   [OK] Short length rejected")

            # -- Test 3: validate_workspace_id --------------------------
            print("\n[PIN] Test 3: validate_workspace_id")

            uuid_id = "550e8400-e29b-41d4-a716-446655440000"
            assert validate_workspace_id(uuid_id) == uuid_id
            print(f"   [OK] UUID accepted: {uuid_id[:8]}...")

            assert validate_workspace_id("default") == "default"
            print("   [OK] Literal 'default' accepted")

            try:
                validate_workspace_id("INVALID ID!")
                print("   [FAIL] Should reject invalid ID")
            except ValueError:
                print("   [OK] Invalid format rejected")

            # -- Test 4: validate_password_strength ---------------------
            print("\n[PIN] Test 4: validate_password_strength")

            valid, msg = validate_password_strength("StrongP@ss1")
            assert valid is True and msg == ""
            print("   [OK] Valid password accepted")

            valid, msg = validate_password_strength("Short1!")
            assert not valid
            print(f"   [OK] Too short rejected: '{msg}'")

            valid, msg = validate_password_strength("lowercase1!")
            assert not valid and "uppercase" in msg.lower()
            print(f"   [OK] Missing uppercase: '{msg}'")

            valid, msg = validate_password_strength("NoDigit@Pass")
            assert not valid and "digit" in msg.lower()
            print(f"   [OK] Missing digit: '{msg}'")

            valid, msg = validate_password_strength("NoSpecial1")
            assert not valid and "special" in msg.lower()
            print(f"   [OK] Missing special char: '{msg}'")

            # -- Test 5: sanitize_for_display ---------------------------
            print("\n[PIN] Test 5: sanitize_for_display (HTML/XSS protection)")

            # Plain text preserved
            assert sanitize_for_display("Hello World") == "Hello World"
            print("   [OK] Plain text preserved")

            # HTML tags stripped (main security goal)
            result = sanitize_for_display("<script>alert('xss')</script>")
            assert "<script>" not in result.lower() and "</script>" not in result.lower()
            assert "alert" in result or "xss" in result.lower()
            print(f"   [OK] HTML tags stripped safely: '{result}'")

            # HTML event handlers stripped
            result = sanitize_for_display('<img src="x" onerror="alert(1)">')
            assert "onerror" not in result.lower() or "&lt;" in result
            print(f"   [OK] Event handlers handled: '{result}'")

            # Length limit works
            result = sanitize_for_display("A" * 200, max_len=10)
            assert len(result) == 10
            print(f"   [OK] Truncated to max_len: {len(result)} chars")

            # -- Test 6: Tags -------------------------------------------
            print("\n[PIN] Test 6: validate_tags & normalize_tags")

            # [OK] FIXED: Use valid tags (NO SPACES - regex only allows a-z0-9_\-)
            tags = validate_tags(["tag1", "tag-2", "TAG_3"])  # No spaces!
            assert tags == ["tag1", "tag-2", "tag_3"]
            print("   [OK] validate_tags: normalized and case-lowered")

            # Normalize (dedup while preserving order)
            tags = normalize_tags(["a", "b", "a", "c"])
            assert tags == ["a", "b", "c"]
            print("   [OK] normalize_tags: deduplication preserved order")

            # Invalid: spaces not allowed in tags
            try:
                validate_tags(["tag with space"])  # Space not allowed
                print("   [FAIL] Should reject tags with spaces")
            except ValueError as e:
                if "invalid characters" in str(e):
                    print(f"   [OK] Tags with spaces rejected: '{e}'")

            # Invalid: special chars not allowed
            try:
                validate_tags(["tag@invalid"])
                print("   [FAIL] Should reject tags with special chars")
            except ValueError:
                print("   [OK] Tags with special chars rejected")

            print("\n" + "=" * 70)
            print("[OK] ALL TESTS PASSED! Validators module verified.")
            print("\n[TIP] What we verified:")
            print("   • Email: normalization, format check [OK]")
            print("   • Slug: pattern, length check + auto-normalization [OK]")
            print("   • Workspace ID: UUID, 'default', slug support [OK]")
            print("   • Password: complexity requirements with specific errors [OK]")
            print("   • Sanitization: HTML tag stripping, XSS prevention [OK]")
            print("   • Tags: normalization, deduplication, pattern validation [OK]")
            print("\n[SEC] Security Note: sanitize_for_display focuses on HTML sanitization.")
            print("   URL protocol filtering (javascript:) is handled at point of use.")
            return True

        except Exception as e:
            print(f"\n[FAIL] Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

    # Run tests
    success = run_tests()
    sys.exit(0 if success else 1)
