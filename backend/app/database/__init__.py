# backend/app/database/__init__.py
"""Database package for DocuMind AI — SQLAlchemy 2.0 async support."""

from __future__ import annotations

from .engine import engine, async_engine, get_sync_session, get_async_session
from .session import get_db, get_async_db

__all__ = [
    "engine",
    "async_engine",
    "get_sync_session",
    "get_async_session",
    "get_db",
    "get_async_db",
]
