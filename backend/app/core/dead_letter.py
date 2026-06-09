# backend/app/core/dead_letter.py
# DVMELTSS-FIX: E - Error handling, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
# ACID-INDEX: E - Error handling (dead-letter must not break main pipeline)
"""
Dead-letter logging utilities for failed OCR/page processing.

Features:
- Never raises — failure logging must not break the main pipeline
- Configurable directory via settings
- Automatic rotation: keeps only last N files to prevent disk fill
- Optional context dict for additional debugging info

Usage:
    from app.core.dead_letter import log_failed_page
    log_failed_page("doc.pdf", page_num=3, error="OCR failed", context={"confidence": 0.3})
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


def _get_dead_letter_dir() -> Path:
    """Get dead-letter directory from settings with fallback."""
    settings = get_settings()
    return Path(settings.dead_letter_dir or "/tmp/documind/dead_letter")


def log_failed_page(
    file_path: str | Path,
    page_num: int,
    error: str,
    context: Optional[dict] = None,
) -> Optional[Path]:
    """
    Log a failed OCR page to the dead-letter directory for later investigation.

    Args:
        file_path: Path to the source document
        page_num: 0-indexed page number that failed
        error: Error message or exception string
        context: Optional dict with additional debug info (OCR confidence, etc.)

    Returns:
        Path to written file, or None if logging failed
    """
    try:
        dead_letter_dir = _get_dead_letter_dir()
        dead_letter_dir.mkdir(parents=True, exist_ok=True)

        # Build record with optional context
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "file_path": str(file_path),
            "filename": Path(file_path).name,
            "page_num": page_num,
            "error": str(error),
            **(context or {}),
        }

        # Generate unique filename with timestamp to avoid collisions
        safe_name = Path(file_path).stem.replace(".", "_")[:50]
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = dead_letter_dir / f"failed_{safe_name}_p{page_num}_{ts}.json"

        # Write atomically via temp file + rename
        temp_file = out_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(record, indent=2, default=str))
        temp_file.rename(out_file)

        logger.warning(f"Dead-letter logged: {out_file.name}")

        # Rotation: keep only last N files to prevent disk fill
        _rotate_dead_letter_files(dead_letter_dir, max_files=100)

        return out_file

    except Exception as e:
        # Never let dead-letter logging break the main pipeline
        logger.debug(f"Dead-letter logging failed (non-critical): {e}")
        return None


def _rotate_dead_letter_files(directory: Path, max_files: int = 100) -> None:
    """
    Remove oldest dead-letter files if count exceeds max_files.

    Args:
        directory: Directory containing dead-letter JSON files
        max_files: Maximum number of files to retain
    """
    try:
        files = sorted(
            directory.glob("failed_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,  # Newest first
        )
        if len(files) > max_files:
            to_delete = files[max_files:]
            for f in to_delete:
                f.unlink(missing_ok=True)
                logger.debug(f"Rotated dead-letter file: {f.name}")
    except Exception as e:
        logger.debug(f"Dead-letter rotation failed (non-critical): {e}")


def list_failed_pages(
    source_file: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """
    List dead-letter records for debugging/monitoring.

    Args:
        source_file: Optional filename filter
        limit: Maximum number of records to return

    Returns:
        List of dead-letter record dicts, newest first
    """
    try:
        dead_letter_dir = _get_dead_letter_dir()
        if not dead_letter_dir.exists():
            return []

        files = sorted(
            dead_letter_dir.glob("failed_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        records = []
        for f in files[:limit]:
            if source_file and source_file not in f.name:
                continue
            try:
                record = json.loads(f.read_text())
                records.append(record)
            except (json.JSONDecodeError, OSError):
                continue  # Skip corrupted files

        return records

    except Exception as e:
        logger.debug(f"Failed to list dead-letter files: {e}")
        return []


# DVMELTSS-M: Explicit module exports
__all__ = ["log_failed_page", "list_failed_pages"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
