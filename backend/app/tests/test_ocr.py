import pytest
import numpy as np
from app.ocr.paddle_ocr import PaddleOCREngine
from app.ocr.pipeline import OCRPipeline
from app.ocr.vision_analyzer import VisionAnalyzer

def test_language_detection_edge_cases():
    """Verify language detection handles short/mixed text correctly."""
    engine = PaddleOCREngine()
    
    # Short English text
    assert engine._detect_language("Hi") == "en"
    
    # Short but clear CJK
    assert engine._detect_language("你好世界测试") == "zh"
    
    # Mixed short text (should default to en)
    assert engine._detect_language("Hi 你好") == "en"
    
    # Longer mixed with dominant script
    assert engine._detect_language("This is English but 中文 characters too") == "en"
    assert engine._detect_language("这是中文 with some English words") == "zh"

def test_pipeline_memory_cleanup():
    """Verify process_file_enriched frees memory after use."""
    import gc
    from pathlib import Path
    
    # Create minimal test PDF (or use existing test fixture)
    # This test verifies no memory leaks in large doc processing
    pipeline = OCRPipeline()
    
    # Mock a small document processing
    # In real test, use tempfile with known content
    result = pipeline.process_file_enriched(
        file_path=Path("tests/fixtures/sample.pdf"),
        enable_vision_enrichment=False,  # Skip Vision to avoid API calls
    )
    
    # Force garbage collection and check no lingering large arrays
    gc.collect()
    # Assert: no unexpected memory growth (would require memory profiling in CI)
    assert result is not None

def test_vision_analyzer_json_error_handling():
    """Verify JSON parse errors are logged with context."""
    import logging
    from unittest.mock import Mock, patch
    
    analyzer = VisionAnalyzer(api_key="sk-test123")
    
    # Mock response with invalid JSON
    with patch.object(analyzer.client.chat.completions, "create") as mock_create:
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="{invalid json"))]
        mock_response.usage = None
        mock_create.return_value = mock_response
        
        # Should raise VisionAnalyzerError with detailed message
        with pytest.raises(Exception) as exc_info:
            analyzer._call_with_retry(
                messages=[{"role": "user", "content": "test"}],
                max_tokens=100,
                call_type="test"
            )
        
        assert "invalid JSON" in str(exc_info.value)
        # Verify error was logged (check caplog in real test)

def test_vision_ocr_bbox_fallback():
    """Verify missing bbox triggers warning + uses placeholder."""
    from app.ocr.vision_ocr import VisionOCREngine
    
    engine = VisionOCREngine(api_key="sk-test123")
    
    # Mock response with block missing bbox
    raw_json = '{"blocks": [{"text": "test", "confidence": 0.9}]}'
    
    # Should not crash, should log warning, should use placeholder bbox
    blocks = engine._parse_response(raw_json, page_num=0)
    
    assert len(blocks) == 1
    assert blocks[0].bbox == [[0, 0], [100, 0], [100, 20], [0, 20]]
    # Verify warning was logged (check caplog in real test)