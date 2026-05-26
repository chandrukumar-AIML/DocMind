"""Compatibility response schema exports.

# ADDED: Keep older imports stable while the canonical models live in
# app.models.common_schemas.
"""
from __future__ import annotations

from app.models.common_schemas import CitationModel, QueryResponse

__all__ = ["CitationModel", "QueryResponse"]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

