"""Unit tests for core input validators (security-relevant, pure functions)."""

import pytest

from app.core.validators import (
    validate_email,
    validate_slug,
    validate_workspace_id,
    validate_password_strength,
    sanitize_for_display,
    validate_tags,
    normalize_tags,
)


class TestEmail:
    def test_normalizes_case_and_whitespace(self):
        assert validate_email("  User@Example.COM ") == "user@example.com"

    @pytest.mark.parametrize("bad", ["", "not-an-email", "a@b", "@nodomain.com"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_email(bad)


class TestWorkspaceId:
    def test_accepts_uuid(self):
        wid = "550e8400-e29b-41d4-a716-446655440000"
        assert validate_workspace_id(wid) == wid

    def test_accepts_literal_default(self):
        assert validate_workspace_id("default") == "default"

    def test_accepts_slug(self):
        assert validate_workspace_id("acme-corp") == "acme-corp"

    @pytest.mark.parametrize("bad", ["", "a" * 65, "Bad Spaces!", "../etc"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_workspace_id(bad)


class TestSlug:
    def test_lowercases_and_trims(self):
        assert validate_slug("  My-Slug  ") == "my-slug"

    @pytest.mark.parametrize("bad", ["ab", "a" * 65, "has space", "UPPER!@#"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_slug(bad)


class TestPasswordStrength:
    def test_strong_password_passes(self):
        ok, msg = validate_password_strength("DemoP@ssw0rd!2026")
        assert ok is True
        assert msg == ""

    @pytest.mark.parametrize(
        "pwd,reason",
        [
            ("Ab1!", "at least"),  # too short (4 chars)
            ("alllowercase1!", "uppercase"),
            ("ALLUPPERCASE1!", "lowercase"),
            ("NoDigitsHere!", "digit"),
            ("NoSpecial1234", "special"),
        ],
    )
    def test_weak_passwords_report_reason(self, pwd, reason):
        ok, msg = validate_password_strength(pwd)
        assert ok is False
        assert reason in msg.lower()


class TestSanitizeForDisplay:
    def test_neutralizes_script_tags(self):
        out = sanitize_for_display("<script>alert('xss')</script>")
        assert "<script>" not in out

    def test_truncates_to_max_len(self):
        out = sanitize_for_display("x" * 500, max_len=50)
        assert len(out) <= 50

    def test_non_string_returns_empty(self):
        assert sanitize_for_display(None) == ""


class TestTags:
    def test_validate_normalizes_and_skips_blanks(self):
        assert validate_tags(["  Legal ", "finance", "   "]) == ["legal", "finance"]

    def test_normalize_dedupes_preserving_order(self):
        assert normalize_tags(["A", "b", "a", "B"]) == ["a", "b"]

    @pytest.mark.parametrize(
        "bad",
        [
            ["good", "bad tag!"],  # invalid char
            ["x" * 51],  # too long
            "not-a-list",  # not a list
            [123],  # non-string element
        ],
    )
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_tags(bad)
