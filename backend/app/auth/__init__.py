
"""
DocuMind AI - Authentication & Authorization Module

Provides:
- JWT handling (encode/decode/verify)
- Password hashing (bcrypt)
- FastAPI route dependencies (get_current_user, require_editor, require_admin)
- Async User/Workspace/Member CRUD store
- SQLAlchemy models with DB-level constraints & indexes

Public API:
    from app.auth import get_current_user, UserStore, AuthenticatedUser, UserRole
"""

from __future__ import annotations

# DVMELTSS-M: Explicit public API surface — prevents accidental internal imports
__all__ = [
    # Models
    "User",
    "Workspace",
    "WorkspaceMember",
    "UserRole",
    # JWT & Crypto
    "create_access_token",
    "create_refresh_token",
    "verify_access_token",
    "hash_password",
    "verify_password",
    # Data Access
    "UserStore",
    # FastAPI Dependencies
    "get_current_user",
    "require_editor",
    "require_admin",
    "AuthenticatedUser",
    # Validators (centralized)
    "validate_email",
    "validate_slug",
    "validate_workspace_id",
    "validate_password_strength",
]

# ASCALE-S: Module metadata for observability, version tracking & debugging
__version__ = "1.2.1"  # FIXED: Bumped for validator integration
__auth_provider__ = "JWT + SQLAlchemy + FastAPI Dependencies"


# DVMELTSS-T: Lazy-import fallback to prevent circular dependency crashes
# during FastAPI startup, Alembic migrations, or pytest collection.
# Python 3.7+ compatible (PEP 562).
def __getattr__(name: str):
    """
    Dynamically resolve imports only when accessed.
    Prevents circular imports between auth ↔ provenance ↔ agent modules.
    """
    # Models
    if name == "User":
        from .models import User

        return User
    if name == "Workspace":
        from .models import Workspace

        return Workspace
    if name == "WorkspaceMember":
        from .models import WorkspaceMember

        return WorkspaceMember
    if name == "UserRole":
        from .models import UserRole

        return UserRole

    # JWT & Crypto
    if name in (
        "create_access_token",
        "create_refresh_token",
        "verify_access_token",
        "hash_password",
        "verify_password",
    ):
        from .jwt_handler import (
            create_access_token,
            create_refresh_token,
            verify_access_token,
            hash_password,
            verify_password,
        )

        return locals()[name]

    # Data Access
    if name == "UserStore":
        from .store import UserStore

        return UserStore

    # FastAPI Dependencies
    if name in (
        "get_current_user",
        "require_editor",
        "require_admin",
        "AuthenticatedUser",
    ):
        from .dependencies import (
            get_current_user,
            require_editor,
            require_admin,
            AuthenticatedUser,
        )

        return locals()[name]

    # Validators (centralized from app.core)
    if name in (
        "validate_email",
        "validate_slug",
        "validate_workspace_id",
        "validate_password_strength",
    ):
        from app.core.validators import (
            validate_email,
            validate_slug,
            validate_workspace_id,
            validate_password_strength,
        )

        return locals()[name]

    # DVMELTSS-M: Clear error for unmapped attributes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# DVMELTSS-L: Module initialization logging for observability
def _log_module_init() -> None:
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Auth module loaded | version={__version__} | provider={__auth_provider__}")


# Auto-log on import (safe — only runs once per process)
_log_module_init()
