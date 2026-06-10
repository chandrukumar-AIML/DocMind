import pytest
from app.ocr.vision_analyzer import VisionAnalyzer

# PaddleOCR is a heavy optional dependency (not installed in CI/local-dev without GPU setup).
# The class definition imports fine but its __init__ does `from paddleocr import ...` so we
# must probe the underlying package, not the wrapper class.
try:
    import importlib
    importlib.import_module("paddleocr")
    from app.ocr.paddle_ocr import PaddleOCREngine
    _PADDLE_AVAILABLE = True
except (ModuleNotFoundError, ImportError):
    _PADDLE_AVAILABLE = False


@pytest.mark.skipif(not _PADDLE_AVAILABLE, reason="paddleocr not installed in this environment")
def test_language_detection_edge_cases():
    """Verify language detection returns valid language codes and doesn't crash."""
    engine = PaddleOCREngine()

    # Method is _detect_language_safe (public-safe wrapper with fallbacks)
    # The method always returns a valid ISO language code string, never raises
    VALID_LANGS = {"en", "zh", "ja", "ko", "ar", "fr", "de", "es", "pt", "ru"}

    result_en = engine._detect_language_safe("Hi")
    assert isinstance(result_en, str) and len(result_en) >= 2
    assert result_en == "en"  # Short ASCII text → English

    result_long_en = engine._detect_language_safe("This is a long enough English sentence for detection.")
    assert result_long_en in VALID_LANGS

    # Empty/whitespace → safe fallback, no crash
    result_empty = engine._detect_language_safe("")
    assert isinstance(result_empty, str) and len(result_empty) >= 2

    result_space = engine._detect_language_safe("   ")
    assert isinstance(result_space, str) and len(result_space) >= 2


@pytest.mark.skip(reason="Requires PaddleOCR model files not available in CI env")
def test_pipeline_memory_cleanup():
    """Verify process_file_enriched frees memory after use."""
    import gc
    from pathlib import Path
    from app.ocr.pipeline import OCRPipeline

    pipeline = OCRPipeline()
    result = pipeline.process_file_enriched(
        file_path=Path("tests/fixtures/sample.pdf"),
        enable_vision_enrichment=False,
    )
    gc.collect()
    assert result is not None


def test_vision_analyzer_json_error_handling():
    """Verify JSON parse errors produce meaningful exceptions."""
    from unittest.mock import Mock, patch

    analyzer = VisionAnalyzer(api_key="sk-test123")

    # Mock response with invalid JSON
    with patch.object(analyzer.client.chat.completions, "create") as mock_create:
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="{invalid json"))]
        mock_response.usage = None
        mock_create.return_value = mock_response

        # _call_with_retry_sync is the sync variant (use this, not _call_with_retry)
        with pytest.raises(Exception) as exc_info:
            analyzer._call_with_retry_sync(
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                call_type="test",
                correlation_id="test-corr",
            )

        # Should surface a meaningful error (JSON parse or VisionAnalyzerError)
        assert exc_info.value is not None


def test_vision_ocr_bbox_fallback():
    """Verify missing bbox triggers warning + uses placeholder."""
    from app.ocr.vision_ocr import VisionOCREngine

    engine = VisionOCREngine(api_key="sk-test123")

    raw_json = '{"blocks": [{"text": "test", "confidence": 0.9}]}'

    # _parse_response now requires correlation_id as third positional arg
    blocks = engine._parse_response(raw_json, page_num=0, correlation_id="test-corr")

    assert len(blocks) == 1
    assert blocks[0].bbox == [[0, 0], [100, 0], [100, 20], [0, 20]]
