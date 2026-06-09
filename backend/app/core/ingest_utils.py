# backend/app/core/ingest_utils.py
# DVMELTSS-FIX: M - Modular, S - Security, V - Validate
# ASCALE-FIX: S - Separation, C - Coupling
# OWASP-FIX: 7 - PII redaction, 9 - Path safety
"""
Shared utilities for ingestion modules.

Centralizes:
- PII redaction patterns for metadata/cell data
- Path traversal prevention helpers
- Async-safe file I/O wrappers
- Formula neutralization for spreadsheet safety

Usage:
    from app.core.ingest_utils import redact_pii, validate_upload_path
"""

from __future__ import annotations

import asyncio
import logging  # ✅ FIXED: Added missing logging import
import re
from pathlib import Path
from typing import Final, Optional, Union

# ✅ FIXED: Added missing import for generate_correlation_id
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)

# DVMELTSS-S: Centralized PII patterns for metadata redaction
_PII_PATTERNS: Final = [
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{16}\b"), "[CARD]"),
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[CARD]"),
]
# OWASP-9: Safe filename pattern — prevent path traversal
_SAFE_FILENAME_PATTERN: Final = re.compile(r"^[a-zA-Z0-9._\-/\\]+$")
# OWASP-1: Dangerous Excel formula prefixes
_DANGEROUS_FORMULA_PREFIXES: Final = frozenset({"=", "+", "-", "@"})
# DVMELTSS-S: File size limits
_MAX_FILE_SIZE_MB: Final = 500


def redact_pii(text: str) -> str:
    """
    Redact common PII patterns from strings.

    Args:
        text: Raw text that may contain PII

    Returns:
        Text with PII replaced by placeholders
    """
    if not isinstance(text, str):
        return str(text) if text is not None else ""

    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)

    return text


def validate_upload_path(
    file_path: Union[str, Path], allowed_root: Optional[Path] = None
) -> tuple[Path, Optional[str]]:
    """
    Validate and sanitize file path to prevent traversal attacks.

    Args:
        file_path: Raw file path string or Path
        allowed_root: Optional root directory to restrict uploads

    Returns:
        (resolved_path, error_message) — error is None if valid
    """
    try:
        path = Path(file_path).resolve(strict=True)
    except FileNotFoundError:
        return Path(file_path), f"File not found: {file_path}"
    except OSError as e:
        return Path(file_path), f"Path validation failed: {e}"

    # Optional: Restrict to specific upload directory
    if allowed_root:
        try:
            allowed = allowed_root.resolve()
            if not str(path).startswith(str(allowed)):
                return Path(file_path), "Path traversal detected"
        except Exception:
            pass  # If allowed_root is invalid, skip this check

    return path, None


def neutralize_formula(value) -> str:
    """
    Neutralize dangerous Excel formulas to prevent code execution.

    Args:
        value: Cell value that may contain a formula

    Returns:
        Safe string representation
    """
    if not isinstance(value, str):
        return str(value) if value is not None else ""

    stripped = value.strip()
    if stripped and stripped[0] in _DANGEROUS_FORMULA_PREFIXES:
        return "'" + value  # Prefix with apostrophe to make literal
    return value


async def read_file_bytes_async(file_path: Path, max_bytes: Optional[int] = None) -> bytes:
    """
    Async-safe file read via thread executor.

    Args:
        file_path: Path to file
        max_bytes: Optional limit on bytes to read

    Returns:
        File content as bytes
    """
    loop = asyncio.get_running_loop()

    def _read():
        with open(file_path, "rb") as f:
            return f.read(max_bytes) if max_bytes else f.read()

    return await loop.run_in_executor(None, _read)


def generate_ingest_correlation_id(prefix: str = "ingest") -> str:
    """Generate correlation ID for ingestion tracing."""
    # ✅ FIXED: Now generate_correlation_id is imported and defined
    return f"{prefix}_{generate_correlation_id()}"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "redact_pii",
    "validate_upload_path",
    "neutralize_formula",
    "read_file_bytes_async",
    "generate_ingest_correlation_id",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
