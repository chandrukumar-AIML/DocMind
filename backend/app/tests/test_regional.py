"""Unit tests for the regional (Indian-language) processor.

This module is pure (regex/string only — no DB, model, or network imports), so
the tests run fast and deterministically without standing up the full app.
"""
import pytest

from app.core.regional_language_processor import (
    validate_pan,
    validate_gstin,
    validate_aadhaar,
    normalize_indian_number,
    normalize_tanglish_query,
    extract_indian_entities,
    parse_indian_date,
    preprocess_regional_query,
    detect_script,
)


class TestIndianIdValidation:
    @pytest.mark.parametrize("pan,expected", [
        ("ABCDE1234F", True),
        ("abcde1234f", True),          # case-insensitive (upcased internally)
        ("  ABCDE1234F  ", True),      # trimmed
        ("INVALID", False),
        ("ABCDE12345", False),         # wrong trailing char
        ("ABC1234567", False),
    ])
    def test_validate_pan(self, pan, expected):
        assert validate_pan(pan) is expected

    @pytest.mark.parametrize("gstin,expected", [
        ("27AAPFU0939F1ZV", True),
        ("BADGSTIN", False),
        ("", False),
    ])
    def test_validate_gstin(self, gstin, expected):
        assert validate_gstin(gstin) is expected

    @pytest.mark.parametrize("aadhaar,expected", [
        ("2345 6789 0123", True),
        ("2345-6789-0123", True),
        ("234567890123", True),
        ("1234 5678 9012", False),     # must not start with 0 or 1
        ("2345 6789", False),          # too short
    ])
    def test_validate_aadhaar(self, aadhaar, expected):
        assert validate_aadhaar(aadhaar) is expected


class TestIndianNumbers:
    def test_crores(self):
        assert normalize_indian_number("5.2 crores") == 52_000_000.0

    def test_lakhs(self):
        assert normalize_indian_number("18 lakhs") == 1_800_000.0

    def test_plain_number_with_commas(self):
        assert normalize_indian_number("1,00,000") == 100000.0

    def test_non_number_returns_none(self):
        assert normalize_indian_number("hello") is None


class TestTanglishAndScript:
    def test_tanglish_expansion(self):
        assert normalize_tanglish_query("aadayam kanam ottam") == "income amount total"

    def test_plain_english_unchanged(self):
        assert normalize_tanglish_query("show me the income") == "show me the income"

    def test_detect_script_latin_is_none(self):
        assert detect_script("plain english text") is None


class TestEntityAndDate:
    def test_extract_pan_and_gstin(self):
        entities = extract_indian_entities("PAN: ABCDE1234F GSTIN: 27AAPFU0939F1ZV")
        assert "ABCDE1234F" in entities["pan"]
        assert "27AAPFU0939F1ZV" in entities["gstin"]

    def test_parse_indian_date_to_iso(self):
        assert parse_indian_date("31/12/2024") == "2024-12-31"
        assert parse_indian_date("01-01-2025") == "2025-01-01"

    def test_parse_invalid_date_returns_none(self):
        assert parse_indian_date("no date here") is None


class TestPreprocessPipeline:
    def test_full_pipeline_shape(self):
        result = preprocess_regional_query("aadayam 5.2 crores PAN ABCDE1234F")
        assert result["original_query"].startswith("aadayam")
        # "aadayam" → "income" via the Tanglish map
        assert "income" in result["normalized_query"]
        # NOTE: the pipeline parses amounts token-by-token, so "5.2 crores" is not
        # combined — "5.2" is captured on its own. This asserts current behaviour.
        assert 5.2 in result["extracted_amounts"]
        assert "ABCDE1234F" in result["extracted_entities"]["pan"]
        assert result["is_multilingual"] is True
