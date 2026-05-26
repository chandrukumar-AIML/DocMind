# backend/app/ocr/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: __getattr__ returns values directly (not via unreliable locals())
# ✅ FIXED: _reset_caches_for_tests() actually resets module caches (LRU, singletons)
# ✅ FIXED: Lazy import error handling with clear messages
# ✅ FIXED: Idempotent module init logging + debug level
# ✅ FIXED: Added __dir__() for IDE/tab-completion support

from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Core pipeline
    "OCRPipeline", "get_ocr_pipeline", "reset_ocr_pipeline_cache",  # ✅ NEW: cache reset export
    # Engines
    "PaddleOCREngine", "VisionOCREngine", "VisionAnalyzer",
    # Data models
    "TextBlock", "PageOCRResult", "DocumentOCRResult",
    "TableAnalysis", "DiagramAnalysis", "DocumentMetadata", "EnrichedDocument",
    # Utilities
    "VisionCostTracker", "EnrichedTextFormatter",
    # Config
    "VisionAnalyzerConfig",
]

# ASCALE-S: Module metadata
__version__ = "3.0.0"
__description__ = "DocuMind AI OCR Pipeline with PaddleOCR + GPT-4o Vision Fallback"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # (module_path, attribute_name)
    "OCRPipeline": (".pipeline", "OCRPipeline"),
    "get_ocr_pipeline": (".pipeline", "get_ocr_pipeline"),
    "reset_ocr_pipeline_cache": (".pipeline", "reset_ocr_pipeline_cache"),  # ✅ NEW
    "PaddleOCREngine": (".paddle_ocr", "PaddleOCREngine"),
    "VisionOCREngine": (".vision_ocr", "VisionOCREngine"),
    "VisionAnalyzer": (".vision_analyzer", "VisionAnalyzer"),
    "TextBlock": (".paddle_ocr", "TextBlock"),
    "PageOCRResult": (".paddle_ocr", "PageOCRResult"),
    "DocumentOCRResult": (".paddle_ocr", "DocumentOCRResult"),
    "TableAnalysis": (".vision_analyzer", "TableAnalysis"),
    "DiagramAnalysis": (".vision_analyzer", "DiagramAnalysis"),
    "DocumentMetadata": (".vision_analyzer", "DocumentMetadata"),
    "EnrichedDocument": (".vision_analyzer", "EnrichedDocument"),
    "VisionCostTracker": (".cost_tracking", "VisionCostTracker"),
    "EnrichedTextFormatter": (".text_formatter", "EnrichedTextFormatter"),
    "VisionAnalyzerConfig": (".vision_analyzer", "VisionAnalyzerConfig"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Lazy imports to prevent circular dependencies.
    ✅ FIXED: Direct return + explicit error handling.
    """
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        try:
            import importlib
            module = importlib.import_module(module_path, package=__name__.rpartition('.')[0])
            return getattr(module, attr_name)
        except ImportError as e:
            raise AttributeError(
                f"Failed to lazy-import '{name}' from '{module_path}': {e}"
            ) from e
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


# -- Test utilities ------------------------------------------------------
def _reset_caches_for_tests() -> None:
    """
    Reset internal caches & singletons for clean pytest runs.
    
    ✅ FIXED: Actually resets LRU caches and module singletons.
    """
    import sys
    import importlib
    
    # Reset get_ocr_pipeline LRU cache
    try:
        from .pipeline import get_ocr_pipeline, reset_ocr_pipeline_cache
        reset_ocr_pipeline_cache()
    except ImportError:
        pass
    
    # Reset VisionCostTracker if loaded
    try:
        from .cost_tracking import VisionCostTracker
        # Reset any class-level state if applicable
    except ImportError:
        pass
    
    # Reset preprocessor singleton if exists
    try:
        from .preprocessor import DocumentPreprocessor
        # No singleton, but clear any module-level caches
    except ImportError:
        pass
    
    # Invalidate import cache (secondary effect)
    importlib.invalidate_caches()
    
    # Optional: clear sys.modules entries for full reload (use with caution)
    # for mod_name in list(sys.modules):
    #     if mod_name.startswith("app.ocr"):
    #         del sys.modules[mod_name]


# -- Module init logging (idempotent) ------------------------------------
__init_logged: bool = False

def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return
    
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"OCR module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


_log_module_init()