
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Final, Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.validators import validate_email
from .models import UserRole
from .jwt_handler import verify_access_token
from app.config import get_settings

logger = logging.getLogger(__name__)
security: Final = HTTPBearer(auto_error=False)

_ROLE_VALUES: Final = frozenset({role.value for role in UserRole})  # type: frozenset[str]
_ADMIN_ROLE_VALUES: Final = UserRole.admin_values()  # workspace_admin + admin (legacy)


# -- Helper: Standardized auth errors ----------------------------------------


def _auth_error(detail: str, status_code: int, corr_id: str, www_authenticate: bool = False) -> HTTPException:
    """Create standardized auth error with correlation ID header."""
    headers = {"X-Correlation-ID": corr_id}
    if www_authenticate and status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return HTTPException(status_code=status_code, detail=detail, headers=headers)


# -- Helper: Workspace ID validator (accepts UUID + "default" + slugs) -------


def _validate_workspace_id(workspace_id: str) -> str:
    """
    Validate workspace ID — accepts UUIDs, "default", and slug format.

    ✅ FIXED: Original validate_workspace_id() was UUID-only, which rejected
    the literal "default" workspace ID used throughout the codebase.
    """
    if not workspace_id or not isinstance(workspace_id, str):
        raise ValueError("workspace_id must be a non-empty string")
    workspace_id = workspace_id.strip()
    if len(workspace_id) > 64:
        raise ValueError("workspace_id too long")

    # Accept UUID format
    try:
        uuid.UUID(workspace_id)
        return workspace_id
    except ValueError:
        pass

    # Accept literal "default" or slug format (letters/digits/hyphens/underscores)
    if workspace_id == "default" or re.match(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$", workspace_id):
        return workspace_id

    raise ValueError(f"Invalid workspace_id format: '{workspace_id}'")


# -- AuthenticatedUser dataclass ---------------------------------------------


@dataclass(frozen=True)
class AuthenticatedUser:
    """
    Injected by auth dependency into every protected route.
    frozen=True prevents accidental mutation during request lifecycle.
    """

    user_id: str
    email: str
    workspace_id: str
    role: str  # admin / editor / viewer
    is_superuser: bool = False
    correlation_id: Optional[str] = field(default=None)

    def __post_init__(self):
        # Validate email
        try:
            validate_email(self.email)
        except ValueError as e:
            raise ValueError(f"Invalid AuthenticatedUser email: {e}")

        try:
            _validate_workspace_id(self.workspace_id)
        except ValueError as e:
            raise ValueError(f"Invalid AuthenticatedUser workspace_id: {e}")

        # Validate role against UserRole enum values
        if self.role not in _ROLE_VALUES:
            valid_roles = "', '".join(sorted(_ROLE_VALUES))
            raise ValueError(f"Invalid role: '{self.role}'. Must be one of: '{valid_roles}'")

    def can_write(self) -> bool:
        return self.role in (
            UserRole.ADMIN.value,
            UserRole.WORKSPACE_ADMIN.value,
            UserRole.EDITOR.value,
        )

    def can_admin(self) -> bool:
        """True for workspace_admin, legacy admin, or superuser."""
        return self.role in _ADMIN_ROLE_VALUES or self.is_superuser

    def can_superadmin(self) -> bool:
        """True only for platform superadmin."""
        return self.is_superuser

    def assert_can_write(self):
        if not self.can_write():
            logger.warning(f"[{self.correlation_id}] Forbidden write attempt: user={self.user_id} role={self.role}")
            raise _auth_error(
                "Write access required (editor or admin role).",
                status.HTTP_403_FORBIDDEN,
                self.correlation_id or "unknown",
            )

    def assert_can_admin(self):
        if not self.can_admin():
            logger.warning(f"[{self.correlation_id}] Forbidden admin attempt: user={self.user_id} role={self.role}")
            raise _auth_error(
                "Workspace admin access required.",
                status.HTTP_403_FORBIDDEN,
                self.correlation_id or "unknown",
            )

    def assert_superadmin(self):
        if not self.can_superadmin():
            logger.warning(f"[{self.correlation_id}] Forbidden superadmin attempt: user={self.user_id}")
            raise _auth_error(
                "Superadmin access required.",
                status.HTTP_403_FORBIDDEN,
                self.correlation_id or "unknown",
            )

    def with_correlation_id(self, correlation_id: str) -> "AuthenticatedUser":
        """
        Return a new AuthenticatedUser instance with the correlation_id set.
        ✅ FIXED: Now works because correlation_id is a normal init-accepting field.
        """
        return AuthenticatedUser(
            user_id=self.user_id,
            email=self.email,
            workspace_id=self.workspace_id,
            role=self.role,
            is_superuser=self.is_superuser,
            correlation_id=correlation_id,
        )


# -- Dev mode bypass (immutable, traceable) ----------------------------------

_DEV_USER: Final = AuthenticatedUser(
    user_id="dev-user-001",
    email="dev@documind.local",
    workspace_id="default",
    role=UserRole.ADMIN.value,
    is_superuser=True,
)


async def _get_dev_user(request: Request) -> AuthenticatedUser:
    """Return immutable dev user with correlation ID for tracing."""
    corr_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())[:8]
    logger.debug(f"[{corr_id}] Dev mode: using immutable dev user (user_id={_DEV_USER.user_id})")
    return _DEV_USER.with_correlation_id(corr_id)


# -- Production auth dependency ----------------------------------------------


def _extract_token(request: Request, credentials: Optional[HTTPAuthorizationCredentials]) -> Optional[str]:
    """
    [OK] FIXED: Extract JWT from httpOnly cookie first, then fall back to Bearer header.

    httpOnly cookies are XSS-proof because JavaScript cannot read them.
    Bearer header fallback maintains backward compatibility for API/Swagger clients.

    Priority:
        1. Cookie "access_token"   ← preferred (XSS-safe, set by login endpoint)
        2. Authorization: Bearer … ← fallback (Swagger UI, API clients, mobile)
    """
    # 1. Try httpOnly cookie (set by login/refresh endpoints — XSS cannot read this)
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token

    # 2. Fall back to Authorization: Bearer header
    if credentials and credentials.credentials:
        return credentials.credentials

    return None


def _api_key_user_from_state(request: Request, corr_id: str) -> Optional[AuthenticatedUser]:
    """
    Build an AuthenticatedUser from API-key context set by ApiKeyAuthMiddleware.

    Returns None when the request was not authenticated via an API key. The synthetic
    identity carries the key's workspace and a role derived from its scopes ("write"
    scope → editor, otherwise viewer), so existing role checks (assert_can_write, etc.)
    work unchanged.
    """
    workspace_id = getattr(request.state, "api_key_workspace_id", None)
    if not workspace_id:
        return None

    scopes = getattr(request.state, "api_key_scopes", None) or []
    key_id = getattr(request.state, "api_key_id", "unknown")
    role = UserRole.EDITOR.value if "write" in scopes else UserRole.VIEWER.value

    api_user = AuthenticatedUser(
        user_id=f"apikey:{key_id}",
        email=f"apikey-{key_id}@apikey.documind.ai",
        workspace_id=workspace_id,
        role=role,
        is_superuser=False,
    )
    logger.info(f"[{corr_id}] Auth success via API key: workspace={workspace_id} scopes={scopes}")
    return api_user.with_correlation_id(corr_id)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthenticatedUser:
    """
    FastAPI dependency: extract and validate JWT, return AuthenticatedUser.

    [OK] FIXED: Reads token from httpOnly cookie first (XSS-safe), then falls
    back to Bearer header for API/Swagger clients. Both paths are supported
    simultaneously — no breaking change for existing API consumers.

    When AUTH_ENABLED=false (dev mode): returns immutable dev user.
    When AUTH_ENABLED=true (production): validates token + claims.
    """
    corr_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())[:8]
    settings = get_settings()
    auth_enabled = getattr(settings, "auth_enabled", True)

    if not auth_enabled:
        return await _get_dev_user(request)

    token = _extract_token(request, credentials)
    if not token:
        # API-key auth: ApiKeyAuthMiddleware validates `Authorization: ApiKey dmk_...`
        # up front and populates request.state. When present, build an
        # AuthenticatedUser from that context so protected routes work for
        # server-to-server clients without a JWT.
        api_user = _api_key_user_from_state(request, corr_id)
        if api_user is not None:
            return api_user
        logger.warning(f"[{corr_id}] Auth failed: missing token (no cookie, no Bearer header)")
        raise _auth_error(
            "Authentication required. Provide Bearer token.",
            401,
            corr_id,
            www_authenticate=True,
        )

    payload = verify_access_token(token)
    if not payload:
        logger.warning(f"[{corr_id}] Auth failed: invalid or expired token")
        raise _auth_error("Invalid or expired token.", 401, corr_id, www_authenticate=True)

    try:
        user_id = payload.get("sub")
        email = payload.get("email")
        workspace_id = payload.get("workspace_id")
        role = payload.get("role")

        if not user_id or not isinstance(user_id, str):
            raise ValueError("Missing or invalid 'sub' claim (user_id)")
        if not email or not isinstance(email, str):
            raise ValueError("Missing or invalid 'email' claim")
        if not workspace_id or not isinstance(workspace_id, str):
            raise ValueError("Missing or invalid 'workspace_id' claim")
        if not role or not isinstance(role, str):
            raise ValueError("Missing or invalid 'role' claim")

        user = AuthenticatedUser(
            user_id=user_id,
            email=email,
            workspace_id=workspace_id,
            role=role,
            is_superuser=bool(payload.get("is_superuser", False)),
        )
    except ValueError as e:
        logger.error(f"[{corr_id}] Auth failed: invalid JWT claims — {e}")
        raise _auth_error(f"Invalid authentication claims: {e}", 401, corr_id)

    logger.info(f"[{corr_id}] Auth success: user={user.user_id[:8]}... workspace={user.workspace_id}")
    return user.with_correlation_id(corr_id)


# -- Role-based dependencies -------------------------------------------------


async def require_editor(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Dependency that requires editor or admin role."""
    user.assert_can_write()
    return user


async def require_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Dependency that requires admin role (workspace_admin or legacy admin)."""
    user.assert_can_admin()
    return user


async def require_workspace_admin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Dependency that requires workspace_admin (or superadmin) role."""
    user.assert_can_admin()
    return user


async def require_superadmin(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    """Dependency that requires platform superadmin role."""
    user.assert_superadmin()
    return user


# ✅ REMOVED: get_correlation_id() — duplicate of security.add_correlation_id middleware
# If you need correlation_id in a route, use: request.state.correlation_id

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.auth.dependencies) ---
# ========================================================================

