# backend/app/ingest/format_detector.py
# DVMELTSS-FIX: V - Validate, S - Security, M - Modular
# OWASP-FIX: 9 - File handling, 1 - Input sanitization
# BATMAN-FIX: M - Memory safety
# ✅ FIXED: Safe filename pattern + input validation + proper immutable pattern

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Optional, Any

logger = logging.getLogger(__name__)

# ✅ FIXED: Only allow safe filename characters (no path separators)
_SAFE_FILENAME_PATTERN: Final = re.compile(r"^[a-zA-Z0-9._\-]+$")


class FileFormat(str, Enum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    BMP = "bmp"
    DOCX = "docx"
    XLSX = "xlsx"
    MP3 = "mp3"
    MP4 = "mp4"
    WAV = "wav"
    M4A = "m4a"
    OGG = "ogg"
    WEBM = "webm"
    UNKNOWN = "unknown"


_MAGIC_SIGNATURES: Final = [
    (b"%PDF", 0, FileFormat.PDF, "application/pdf"),
    (
        b"PK\x03\x04",
        0,
        FileFormat.DOCX,
        "application/vnd.openxmlformats-officedocument",
    ),
    (b"\x89PNG\r\n\x1a\n", 0, FileFormat.PNG, "image/png"),
    (b"\xff\xd8\xff", 0, FileFormat.JPEG, "image/jpeg"),
    (b"II*\x00", 0, FileFormat.TIFF, "image/tiff"),
    (b"MM\x00*", 0, FileFormat.TIFF, "image/tiff"),
    (b"BM", 0, FileFormat.BMP, "image/bmp"),
    (b"ID3", 0, FileFormat.MP3, "audio/mpeg"),
    (b"\xff\xfb", 0, FileFormat.MP3, "audio/mpeg"),
    (b"\xff\xf3", 0, FileFormat.MP3, "audio/mpeg"),
    (b"RIFF", 0, FileFormat.WAV, "audio/wav"),
    (b"OggS", 0, FileFormat.OGG, "audio/ogg"),
    (b"\x1aE\xdf\xa3", 0, FileFormat.WEBM, "video/webm"),
]

_EXTENSION_MAP: Final = {
    ".pdf": (FileFormat.PDF, "application/pdf"),
    ".png": (FileFormat.PNG, "image/png"),
    ".jpg": (FileFormat.JPEG, "image/jpeg"),
    ".jpeg": (FileFormat.JPEG, "image/jpeg"),
    ".tiff": (FileFormat.TIFF, "image/tiff"),
    ".tif": (FileFormat.TIFF, "image/tiff"),
    ".bmp": (FileFormat.BMP, "image/bmp"),
    ".docx": (
        FileFormat.DOCX,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    ".xlsx": (
        FileFormat.XLSX,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ),
    ".mp3": (FileFormat.MP3, "audio/mpeg"),
    ".mp4": (FileFormat.MP4, "video/mp4"),
    ".wav": (FileFormat.WAV, "audio/wav"),
    ".m4a": (FileFormat.M4A, "audio/mp4"),
    ".ogg": (FileFormat.OGG, "audio/ogg"),
    ".webm": (FileFormat.WEBM, "video/webm"),
}

_AUDIO_VIDEO_FORMATS: Final = {
    FileFormat.MP3,
    FileFormat.MP4,
    FileFormat.WAV,
    FileFormat.M4A,
    FileFormat.OGG,
    FileFormat.WEBM,
}
_IMAGE_FORMATS: Final = {
    FileFormat.PNG,
    FileFormat.JPEG,
    FileFormat.TIFF,
    FileFormat.BMP,
}
_DOC_FORMATS: Final = {FileFormat.PDF, FileFormat.DOCX}
_SHEET_FORMATS: Final = {FileFormat.XLSX}


@dataclass(frozen=True)
class DetectedFormat:
    """Immutable format detection result."""

    format: FileFormat
    mime_type: str
    is_image: bool
    is_audio_video: bool
    is_document: bool
    is_spreadsheet: bool
    confidence: float
    extension: str

    def __post_init__(self):
        # ✅ FIXED: Proper immutable pattern for frozen dataclass
        if not (0.0 <= self.confidence <= 1.0):
            # Use object.__setattr__ for frozen dataclass
            object.__setattr__(self, "confidence", max(0.0, min(1.0, self.confidence)))


# ✅ NEW: Input validation helper
def _validate_detect_inputs(
    file_bytes: Optional[bytes],
    filename: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate format detection inputs before processing."""
    if file_bytes is None or not isinstance(file_bytes, bytes):
        return False, "file_bytes must be a non-empty bytes object"
    if filename is None or not isinstance(filename, str) or not filename.strip():
        return False, "filename must be a non-empty string"
    return True, ""


class FormatDetector:
    """Detects file format from magic bytes + extension."""

    def detect(self, file_bytes: bytes, filename: str) -> DetectedFormat:
        """Detect format from file content and name."""
        corr_id = "format_detect"

        # ✅ Validate inputs
        is_valid, error = _validate_detect_inputs(file_bytes, filename, corr_id)
        if not is_valid:
            logger.error(f"[{corr_id}] Invalid detect inputs: {error}")
            return self._build_unknown("")

        # ✅ FIXED: Safe filename check (no path traversal)
        if not _SAFE_FILENAME_PATTERN.match(filename):
            logger.warning(f"[{corr_id}] Potentially unsafe filename: {filename}")
            return self._build_unknown(Path(filename).suffix.lower())

        suffix = Path(filename).suffix.lower()

        # ✅ FIXED: Safe magic signature matching with length checks
        for magic, offset, fmt, mime in _MAGIC_SIGNATURES:
            if len(file_bytes) > offset + len(magic) and file_bytes[offset : offset + len(magic)] == magic:
                # Handle DOCX/XLSX distinction
                if fmt == FileFormat.DOCX:
                    if suffix == ".xlsx":
                        fmt, mime = (
                            FileFormat.XLSX,
                            _EXTENSION_MAP.get(".xlsx", (FileFormat.XLSX, ""))[1],
                        )
                    else:
                        # Safe mime type lookup
                        ext_info = _EXTENSION_MAP.get(suffix)
                        if ext_info:
                            mime = ext_info[1]

                return self._build(fmt, mime, suffix, confidence=1.0)

        # Fallback: extension-based detection with reduced confidence
        if suffix in (".mp4", ".m4a"):
            fmt, mime = _EXTENSION_MAP.get(suffix, (FileFormat.UNKNOWN, ""))
            return self._build(fmt, mime, suffix, confidence=0.9)

        if suffix in _EXTENSION_MAP:
            fmt, mime = _EXTENSION_MAP[suffix]
            return self._build(fmt, mime, suffix, confidence=0.8)

        logger.warning(f"[{corr_id}] Unknown format: {filename}")
        return self._build_unknown(suffix)

    @staticmethod
    def _build(fmt: FileFormat, mime: str, extension: str, confidence: float) -> DetectedFormat:
        # ✅ FIXED: Safe defaults + sanitization
        safe_mime = mime if mime and isinstance(mime, str) else "application/octet-stream"
        safe_ext = extension if extension and isinstance(extension, str) else ""
        safe_confidence = max(0.0, min(1.0, confidence))

        return DetectedFormat(
            format=fmt,
            mime_type=safe_mime,
            is_image=fmt in _IMAGE_FORMATS,
            is_audio_video=fmt in _AUDIO_VIDEO_FORMATS,
            is_document=fmt in _DOC_FORMATS,
            is_spreadsheet=fmt in _SHEET_FORMATS,
            confidence=safe_confidence,
            extension=safe_ext,
        )

    @staticmethod
    def _build_unknown(extension: str) -> DetectedFormat:
        # ✅ FIXED: Safe defaults
        safe_ext = extension if extension and isinstance(extension, str) else ""
        return DetectedFormat(
            format=FileFormat.UNKNOWN,
            mime_type="application/octet-stream",
            is_image=False,
            is_audio_video=False,
            is_document=False,
            is_spreadsheet=False,
            confidence=0.0,
            extension=safe_ext,
        )


def get_format_detector_metadata() -> dict[str, Any]:
    """✅ NEW: Return format detector metadata for debugging."""
    return {
        "supported_formats": [f.value for f in FileFormat],
        "magic_signatures_count": len(_MAGIC_SIGNATURES),
        "extension_mappings_count": len(_EXTENSION_MAP),
        "safe_filename_pattern": _SAFE_FILENAME_PATTERN.pattern,
        "audio_video_formats": [f.value for f in _AUDIO_VIDEO_FORMATS],
        "image_formats": [f.value for f in _IMAGE_FORMATS],
        "doc_formats": [f.value for f in _DOC_FORMATS],
        "sheet_formats": [f.value for f in _SHEET_FORMATS],
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "FormatDetector",
    "FileFormat",
    "DetectedFormat",
    "get_format_detector_metadata",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
