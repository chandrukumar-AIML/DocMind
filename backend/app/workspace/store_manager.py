"""Compatibility exports for workspace resource management.

# ADDED: Older routes imported app.workspace.store_manager; the canonical
# implementation lives in app.workspace.manager.
"""

from __future__ import annotations

from app.workspace.manager import (
    WorkspaceManager,
    WorkspaceResources,
    get_workspace_metadata,
)

__all__ = ["WorkspaceManager", "WorkspaceResources", "get_workspace_metadata"]
# Local smoke test entry point. Run: python -m

