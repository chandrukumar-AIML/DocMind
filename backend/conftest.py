from __future__ import annotations

import sys
from pathlib import Path

# FIXED: Ensure the `backend` package root is importable when running pytest from the workspace root.
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
