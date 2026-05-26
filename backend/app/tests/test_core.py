import pytest
from pathlib import Path
from app.core.exceptions import DocuMindError, ValidationError
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
    # Test message patterns
    assert is_insufficient_quota_error(Exception("You exceeded your current quota"))
    assert is_insufficient_quota_error(Exception("insufficient_quota"))
    
    # Test RateLimitError with quota message
    quota_exc = RateLimitError(message="You exceeded your current quota", response=None, body=None)
    assert is_insufficient_quota_error(quota_exc)
    
    # Test error type classification
    assert get_openai_error_type(quota_exc) == "quota"

def test_dead_letter_rotation(tmp_path):
    """Verify dead-letter rotation keeps only N files."""
    from app.core.dead_letter import log_failed_page, _get_dead_letter_dir
    import json
    
    # Mock settings to use tmp_path
    import app.config
    original_get = app.config.get_settings
    class MockSettings:
        dead_letter_dir = str(tmp_path)
    app.config.get_settings = lambda: MockSettings()
    
    try:
        # Log 5 files
        for i in range(5):
            log_failed_page(f"test_{i}.pdf", 0, f"Error {i}")
        
        # Verify all exist
        files = list(tmp_path.glob("failed_*.json"))
        assert len(files) == 5
        
        # Configure rotation to keep only 3
        from app.core.dead_letter import _rotate_dead_letter_files
        _rotate_dead_letter_files(tmp_path, max_files=3)
        
        # Verify only 3 remain
        files = list(tmp_path.glob("failed_*.json"))
        assert len(files) == 3
        
    finally:
        app.config.get_settings = original_get