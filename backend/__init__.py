# backend/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
"""
DocuMind AI — Backend Package Root

This file ensures the `backend` package is importable when:
- Running pytest from workspace root
- Importing from CLI scripts
- Deploying to Railway/Docker

Usage:
    import sys
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
"""
from __future__ import annotations

import sys
from pathlib import Path

# FIXED: Ensure backend root is in sys.path for pytest/CLI imports
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# DVMELTSS-T: Test hook for clean pytest runs
def _reset_caches_for_tests() -> None:
    """Reset module-level caches for isolated test runs."""
    import importlib
    for mod in list(sys.modules.keys()):
        if mod.startswith("app.") and mod in sys.modules:
            try:
                importlib.reload(sys.modules[mod])
            except Exception:
                pass

# DVMELTSS-M: Explicit exports
__all__ = ["ROOT_DIR", "_reset_caches_for_tests"]