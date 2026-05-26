# backend/app/ingest/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling
# ✅ FIXED: Direct return in __getattr__ + error handling + idempotent logging

"""
DocuMind AI - Multi-Format Ingestion Pipeline

Handles routing, validation, and extraction for:
- PDFs & Images (via OCR pipeline + Handwriting fallback)
- Audio/Video (via Whisper transcription + speaker diarization)
- Word Documents (via python-docx with PII-safe metadata extraction)
- Excel Spreadsheets (via pandas + formula sanitization)
- Format Detection (magic bytes + extension fallback)

Public API:
    from app.ingest import UniversalIngestionPipeline, IngestionResult, FormatDetector
"""
from __future__ import annotations
from typing import Any

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Core Pipeline
    "UniversalIngestionPipeline", "IngestionResult",
    # Format Detection
    "FormatDetector", "FileFormat", "DetectedFormat",
    # Extractors
    "AudioTranscriber", "TranscriptionResult",
    "DocxExtractor", "DocxContent",
    "XlsxExtractor", "XlsxContent",
    "HandwritingOCR", "HandwritingResult",
    # Metadata helpers
    "get_ingest_metadata",
]

# ASCALE-S: Module metadata for observability & version tracking
__version__ = "2.1.0"
__description__ = "DocuMind AI Multi-Format Document Ingestion Pipeline"
__supported_formats__ = "PDF, PNG, JPG, TIFF, DOCX, XLSX, MP3, MP4, WAV, M4A, OGG, WEBM"


# -- Lazy import mapping for __getattr__ ---------------------------------
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # Core Pipeline
    "UniversalIngestionPipeline": (".universal_ingestion", "UniversalIngestionPipeline"),
    "IngestionResult": (".universal_ingestion", "IngestionResult"),
    # Format Detection
    "FormatDetector": (".format_detector", "FormatDetector"),
    "FileFormat": (".format_detector", "FileFormat"),
    "DetectedFormat": (".format_detector", "DetectedFormat"),
    # Audio Transcription
    "AudioTranscriber": (".audio_transcriber", "AudioTranscriber"),
    "TranscriptionResult": (".audio_transcriber", "TranscriptionResult"),
    # Document Extractors
    "DocxExtractor": (".docx_extractor", "DocxExtractor"),
    "DocxContent": (".docx_extractor", "DocxContent"),
    "XlsxExtractor": (".xlsx_extractor", "XlsxExtractor"),
    "XlsxContent": (".xlsx_extractor", "XlsxContent"),
    # Handwriting OCR
    "HandwritingOCR": (".handwriting_ocr", "HandwritingOCR"),
    "HandwritingResult": (".handwriting_ocr", "HandwritingResult"),
}


def __getattr__(name: str) -> Any:
    """
    DVMELTSS-T: Dynamically resolve imports only when accessed.
    ✅ FIXED: Direct return + explicit error handling.
    
    Prevents circular imports between ingest ↔ ocr ↔ extraction ↔ agent modules.
    Enables pytest to collect tests without initializing heavy parsers.
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
    
    if name == "get_ingest_metadata":
        from .universal_ingestion import get_ingestion_metadata
        return get_ingestion_metadata
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """
    ✅ NEW: Enable IDE/tab-completion for lazy exports.
    Returns list of all public names (static + lazy).
    """
    return sorted(set(__all__))


def _reset_caches_for_tests() -> None:
    """Reset internal caches & singletons for clean pytest runs."""
    import importlib
    import sys
    
    # Invalidate import caches
    for mod_name in [
        ".universal_ingestion", ".format_detector", ".audio_transcriber",
        ".docx_extractor", ".xlsx_extractor", ".handwriting_ocr"
    ]:
        try:
            importlib.invalidate_caches()
        except Exception:
            pass
    
    # ✅ FIXED: Reset module-level singletons if loaded
    try:
        from . import universal_ingestion
        if hasattr(universal_ingestion, "_pipeline_instance"):
            universal_ingestion._pipeline_instance = None
    except ImportError:
        pass


# DVMELTSS-L: Module initialization logging for observability
__init_logged: bool = False

def _log_module_init() -> None:
    """Log module load — idempotent to avoid spam in multi-worker setups."""
    global __init_logged
    if __init_logged:
        return
    
    import logging
    logger = logging.getLogger(__name__)
    logger.debug(  # ✅ Use debug level to avoid prod log spam
        f"Ingest module loaded | version={__version__} | {__description__}"
    )
    __init_logged = True


# Auto-log on import (safe — only runs once per process)
_log_module_init()


# ✅ NEW: Metadata helper for monitoring
def get_ingest_metadata() -> dict[str, Any]:
    """Return ingest module metadata for debugging."""
    from .universal_ingestion import get_ingestion_metadata as _get_universal_meta
    from .format_detector import get_format_detector_metadata as _get_format_meta
    from .xlsx_extractor import get_xlsx_metadata as _get_xlsx_meta
    from .docx_extractor import get_docx_metadata as _get_docx_meta
    from .audio_transcriber import get_audio_metadata as _get_audio_meta
    
    return {
        "version": __version__,
        "description": __description__,
        "supported_formats": __supported_formats__,
        "components": {
            "universal": _get_universal_meta(),
            "format_detector": _get_format_meta(),
            "xlsx": _get_xlsx_meta(),
            "docx": _get_docx_meta(),
            "audio": _get_audio_meta(),
        },
        "features": [
            "format_detection",
            "ocr_pipeline",
            "whisper_transcription",
            "speaker_diarization",
            "async_safe",
            "resource_cleanup",
            "pii_redaction",
            "formula_sanitization",
        ],
    }