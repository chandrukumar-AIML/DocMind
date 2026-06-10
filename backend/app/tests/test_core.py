from unittest.mock import patch, Mock
from app.core.exceptions import ValidationError
from app.core.openai_errors import is_insufficient_quota_error, get_openai_error_type
from openai import RateLimitError


def test_exception_api_response():
    """Verify exceptions produce consistent API error format."""
    exc = ValidationError("Invalid email format", context={"field": "email"})
    response = exc.to_api_response()
    assert response["error"] == "VALIDATION_ERROR"
    assert response["status_code"] == 422
    assert response["context"]["field"] == "email"


def test_quota_error_detection():
    """Verify quota error patterns are detected."""
    # Test plain exception message patterns
    assert is_insufficient_quota_error(Exception("You exceeded your current quota"))
    assert is_insufficient_quota_error(Exception("insufficient_quota"))

    # Test RateLimitError with quota message — use a proper mock response
    # because openai SDK >= 1.0 requires response.request to exist
    mock_response = Mock()
    mock_response.request = Mock()
    mock_response.status_code = 429
    mock_response.headers = {}
    quota_exc = RateLimitError(
        message="You exceeded your current quota",
        response=mock_response,
        body={"error": {"type": "insufficient_quota"}},
    )
    assert is_insufficient_quota_error(quota_exc)
    assert get_openai_error_type(quota_exc) == "quota"


def test_dead_letter_rotation(tmp_path):
    """Verify dead-letter rotation keeps only N files."""
    from app.core.dead_letter import log_failed_page, _rotate_dead_letter_files

    # Patch dead_letter's get_settings to redirect to tmp_path
    class MockSettings:
        dead_letter_dir = str(tmp_path)

    with patch("app.core.dead_letter.get_settings", return_value=MockSettings()):
        for i in range(5):
            log_failed_page(f"test_{i}.pdf", 0, f"Error {i}")

        files = list(tmp_path.glob("failed_*.json"))
        assert len(files) == 5

        # Rotate to keep only 3
        _rotate_dead_letter_files(tmp_path, max_files=3)

        files = list(tmp_path.glob("failed_*.json"))
        assert len(files) == 3
