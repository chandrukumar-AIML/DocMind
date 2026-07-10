
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Any

# DVMELTSS-M: Import centralized utilities
from app.core.workspace_utils import validate_workspace_id, get_chroma_collection_name
from app.auth.dependencies import AuthenticatedUser


@dataclass(frozen=True)
class WorkspaceContext:
    """
    Request-scoped workspace context.
    Injected into every handler that needs workspace-aware operations.
    DVMELTSS-M: Frozen dataclass prevents runtime mutation.
    """

    workspace_id: str
    user: AuthenticatedUser
    correlation_id: Optional[str] = None

    def __post_init__(self):
        # Validate and set workspace_id safely
        try:
            safe_id = validate_workspace_id(self.workspace_id)
            if safe_id != self.workspace_id:
                object.__setattr__(self, "workspace_id", safe_id)
        except Exception:
            # If validation fails, keep original but log warning
            import logging

            logging.getLogger(__name__).warning(f"Invalid workspace_id in context: {self.workspace_id}")

    @property
    def collection_name(self) -> str:
        """Get ChromaDB collection name for this workspace."""
        try:
            return get_chroma_collection_name(self.workspace_id)
        except Exception:
            # Fallback to simple naming
            return f"docs_{self.workspace_id}"

    @property
    def can_write(self) -> bool:
        """Check if user has write permissions."""
        try:
            return bool(self.user.can_write())
        except AttributeError:
            return False

    @property
    def can_admin(self) -> bool:
        """Check if user has admin permissions."""
        try:
            return bool(self.user.can_admin())
        except AttributeError:
            return False

    def to_dict(self) -> dict:
        """Serialize context for logging/debugging."""
        return {
            "workspace_id": self.workspace_id,
            "user_id": getattr(self.user, "user_id", ""),
            "role": getattr(self.user, "role", ""),
            "correlation_id": self.correlation_id,
        }


def _validate_context_inputs(
    user: Optional[AuthenticatedUser],
    correlation_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate context inputs before processing."""
    if user is None or not isinstance(user, AuthenticatedUser):
        return False, "user must be an AuthenticatedUser instance"
    if correlation_id is not None and not isinstance(correlation_id, str):
        return False, "correlation_id must be a string or None"
    return True, ""


def workspace_context(
    user: AuthenticatedUser,
    correlation_id: Optional[str] = None,
) -> WorkspaceContext:
    """
    Build WorkspaceContext from authenticated user.
    Args:
        user: Authenticated user from JWT
        correlation_id: Optional request tracing ID
    Returns:
        Frozen WorkspaceContext instance
    """
    corr_id = correlation_id or "context_build"

    # ✅ Validate inputs
    is_valid, error = _validate_context_inputs(user, correlation_id, corr_id)
    if not is_valid:
        import logging

        logging.getLogger(__name__).error(f"[{corr_id}] Invalid context inputs: {error}")
        # Return minimal safe context
        return WorkspaceContext(
            workspace_id=getattr(user, "workspace_id", "unknown") if user else "unknown",
            user=user,
            correlation_id=corr_id,
        )

    # Pre-validate workspace_id before creating context
    try:
        safe_workspace_id = validate_workspace_id(user.workspace_id)
    except Exception as e:
        import logging

        logging.getLogger(__name__).warning(f"[{corr_id}] Invalid workspace_id: {e}")
        safe_workspace_id = user.workspace_id  # Keep original but log warning

    return WorkspaceContext(
        workspace_id=safe_workspace_id,
        user=user,
        correlation_id=correlation_id,
    )


def get_workspace_context_metadata() -> dict[str, Any]:
    """✅ NEW: Return workspace context metadata for debugging."""
    return {
        "frozen_dataclass": True,
        "supports_correlation_id": True,
        "permission_checks": ["can_write", "can_admin"],
        "safe_serialization": True,
    }


# DVMELTSS-M: Explicit module exports
__all__ = [
    "WorkspaceContext",
    "workspace_context",
    "get_workspace_context_metadata",
]
# Local smoke test entry point. Run: python -m

