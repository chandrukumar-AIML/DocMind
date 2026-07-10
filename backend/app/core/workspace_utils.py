"""
Shared utilities for workspace management in DocuMind AI.

Centralizes:
- Workspace ID validation and sanitization
- Collection/namespace name generation
- Resource path construction with settings
- Async-safe provisioning helpers

Usage:
    from app.core.workspace_utils import validate_workspace_id, get_chroma_collection_name
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Optional

from app.config import get_settings
from app.core.ids import generate_correlation_id

# DVMELTSS-S: Workspace ID validation pattern
_WORKSPACE_ID_PATTERN: Final = re.compile(r"^[a-z0-9_-]{3,64}$")
_MAX_WORKSPACE_ID_LENGTH: Final = 64


def validate_workspace_id(workspace_id: str) -> str:
    """
    Validate and sanitize workspace identifier.

    Args:
        workspace_id: Raw workspace ID string

    Returns:
        Validated, lowercased workspace ID

    Raises:
        ValueError: If ID format is invalid
    """
    if not workspace_id or not isinstance(workspace_id, str):
        raise ValueError("workspace_id must be a non-empty string")

    safe_id = workspace_id.lower().strip()

    if not _WORKSPACE_ID_PATTERN.match(safe_id):
        raise ValueError("workspace_id must be 3-64 chars, lowercase letters/numbers/hyphens/underscores only")

    return safe_id


def get_chroma_collection_name(workspace_id: str) -> str:
    """Generate ChromaDB collection name for workspace."""
    safe_id = validate_workspace_id(workspace_id)
    return f"docs_{safe_id}"


def get_neo4j_namespace(workspace_id: str) -> str:
    """Generate Neo4j namespace identifier for workspace."""
    return validate_workspace_id(workspace_id)


def get_bm25_index_path(workspace_id: str, persist_dir: Optional[str] = None) -> Path:
    """
    Generate BM25 index file path for workspace.

    Args:
        workspace_id: Validated workspace ID
        persist_dir: Optional override for cache directory

    Returns:
        Path object for BM25 index file
    """
    safe_id = validate_workspace_id(workspace_id)
    settings = get_settings()
    base_dir = Path(persist_dir) if persist_dir else Path(getattr(settings, "cache_dir", None) or ".cache")
    return base_dir / "bm25" / f"bm25_{safe_id}.pkl"


def get_embeddings_cache_path(workspace_id: str, persist_dir: Optional[str] = None) -> Path:
    """Generate embeddings cache directory path for workspace."""
    safe_id = validate_workspace_id(workspace_id)
    settings = get_settings()
    base_dir = Path(persist_dir) if persist_dir else Path(getattr(settings, "cache_dir", None) or ".cache")
    return base_dir / "embeddings" / safe_id


def get_faiss_index_path(workspace_id: str, persist_dir: Optional[str] = None) -> Path:
    """
    Generate FAISS index base path for workspace (mirrors get_bm25_index_path).

    The returned path is virtual — FAISSVectorStore treats `.parent` as the folder
    passed to save_local/load_local and `.stem` as the index_name, so this file never
    needs to exist itself; only `<stem>.faiss`/`<stem>.pkl` get written.
    """
    safe_id = validate_workspace_id(workspace_id)
    settings = get_settings()
    # Matches today's real global default (./data/faiss/index.bin lives under data_dir),
    # so FAISSVectorStore's path-containment check (against faiss_index_path's parent)
    # keeps working unmodified for workspace-scoped paths.
    base_dir = Path(persist_dir) if persist_dir else Path(getattr(settings, "data_dir", None) or "./data")
    return base_dir / "faiss" / f"faiss_{safe_id}.bin"


def generate_workspace_correlation_id(prefix: str = "workspace") -> str:
    """Generate correlation ID for workspace operations."""
    return f"{prefix}_{generate_correlation_id()}"


# DVMELTSS-M: Explicit module exports
__all__ = [
    "validate_workspace_id",
    "get_chroma_collection_name",
    "get_neo4j_namespace",
    "get_bm25_index_path",
    "get_faiss_index_path",
    "get_embeddings_cache_path",
    "generate_workspace_correlation_id",
]
# Local smoke test entry point. Run: python -m

