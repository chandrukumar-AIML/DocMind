# backend/app/core/time_utils.py
# DVMELTSS-FIX: M - Modular, T - Time handling
# ASCALE-FIX: S - Separation
"""
Centralized time utilities for DocuMind AI.

Provides timezone-aware UTC timestamp generation.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC timestamp — timezone-aware."""
    return datetime.now(timezone.utc)


def format_iso(dt: datetime | None) -> str:
    """Format datetime as ISO 8601 string, or empty string if None."""
    return dt.isoformat() if dt else ""


# DVMELTSS-M: Explicit module exports
__all__ = ["utcnow", "format_iso"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

