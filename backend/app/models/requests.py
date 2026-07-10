"""Compatibility request schema exports.

# ADDED: Keep older imports stable while the canonical models live in
# app.models.common_schemas.
"""
from __future__ import annotations

from app.models.common_schemas import ChatMessage, IngestRequest, QueryRequest

__all__ = ["ChatMessage", "IngestRequest", "QueryRequest"]
# Local smoke test entry point. Run: python -m 

