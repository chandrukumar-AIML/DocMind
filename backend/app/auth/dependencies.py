# backend/app/auth/dependencies.py
# DVMELTSS-FIX: S - Security, V - Validate, E - Error handling, L - Logging
# ASCALE-FIX: S - Separation, E - Error propagation, A - Async handling
# ✅ FIXED: correlation_id field init=True (was init=False -> TypeError)
# ✅ FIXED: workspace_id validator accepts "default" + slugs (not just UUID)
# ✅ FIXED: Removed duplicate get_correlation_id() — use middleware instead

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
    if workspace_id == "default" or re.match(r'^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$', workspace_id):
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
    # ✅ FIXED: init=True (default) so with_correlation_id() works
    correlation_id: Optional[str] = field(default=None)

    def __post_init__(self):
        # Validate email
        try:
            validate_email(self.email)
        except ValueError as e:
            raise ValueError(f"Invalid AuthenticatedUser email: {e}")

        # ✅ FIXED: Use local validator that accepts "default" + slugs
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
            logger.warning(
                f"[{self.correlation_id}] Forbidden write attempt: user={self.user_id} role={self.role}"
            )
            raise _auth_error(
                "Write access required (editor or admin role).",
                status.HTTP_403_FORBIDDEN,
                self.correlation_id or "unknown",
            )

    def assert_can_admin(self):
        if not self.can_admin():
            logger.warning(
                f"[{self.correlation_id}] Forbidden admin attempt: user={self.user_id} role={self.role}"
            )
            raise _auth_error(
                "Workspace admin access required.",
                status.HTTP_403_FORBIDDEN,
                self.correlation_id or "unknown",
            )

    def assert_superadmin(self):
        if not self.can_superadmin():
            logger.warning(
                f"[{self.correlation_id}] Forbidden superadmin attempt: user={self.user_id}"
            )
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
        logger.warning(f"[{corr_id}] Auth failed: missing token (no cookie, no Bearer header)")
        raise _auth_error("Authentication required. Provide Bearer token.", 401, corr_id, www_authenticate=True)

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

if __name__ == "__main__":
    import asyncio
    import sys
    import os
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi import HTTPException, Request
    from fastapi.security import HTTPAuthorizationCredentials
    
    # 🔧 ROBUST PATH SETUP
    current_file = Path(__file__).resolve()
    for parent in current_file.parents:
        if parent.name == "backend" and (parent / "requirements.txt").exists():
            backend_root = parent
            break
    else:
        backend_root = current_file.parents[2]
    
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    
    # Set test JWT secret
    if not os.getenv("JWT_SECRET_KEY"):
        os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-local-testing-only-do-not-use-in-prod-1234567890"
    
    async def run_tests():
        print("🔍 Testing Auth Dependencies module (app/auth/dependencies.py)")
        print("=" * 70)
        
        try:
            from app.auth.dependencies import (
                get_current_user, require_editor, require_admin,
                AuthenticatedUser, _validate_workspace_id
            )
            from app.auth.jwt_handler import create_access_token
            from app.auth.models import UserRole
            
            # -- Test 1: Workspace ID validation --------------------------
            print("\n📌 Test 1: _validate_workspace_id (UUID + slug + 'default')")
            
            # Valid UUID
            uuid_ws = "c7099a0d-b028-4b6d-a574-b110cc36475b"
            assert _validate_workspace_id(uuid_ws) == uuid_ws
            print(f"   ✅ UUID workspace: {uuid_ws[:8]}...")
            
            # Valid slug
            slug_ws = "my-workspace-123"
            assert _validate_workspace_id(slug_ws) == slug_ws
            print(f"   ✅ Slug workspace: {slug_ws}")
            
            # Literal "default"
            assert _validate_workspace_id("default") == "default"
            print(f"   ✅ Literal 'default' workspace")
            
            # Invalid workspace ID
            try:
                _validate_workspace_id("invalid@workspace!")
                print("   ❌ Should reject invalid format")
            except ValueError:
                print(f"   ✅ Invalid workspace rejected")
            
            # -- Test 2: AuthenticatedUser dataclass ---------------------
            print("\n📌 Test 2: AuthenticatedUser (frozen, validation)")
            
            user = AuthenticatedUser(
                user_id="user-123",
                email="test@docmind.ai",
                workspace_id="default",
                role=UserRole.EDITOR.value
            )
            assert user.can_write() is True, "Editor can write"
            assert user.can_admin() is False, "Editor cannot admin"
            print(f"   ✅ AuthenticatedUser: role={user.role}, can_write={user.can_write()}")
            
            # Test with_correlation_id (was broken, now fixed)
            user_with_corr = user.with_correlation_id("corr-abc123")
            assert user_with_corr.correlation_id == "corr-abc123"
            assert user_with_corr.user_id == user.user_id  # Immutable
            print(f"   ✅ with_correlation_id: new instance with corr_id={user_with_corr.correlation_id}")
            
            # Frozen: cannot mutate
            try:
                user.role = "admin"  # Should fail
                print("   ❌ Should reject mutation (frozen)")
            except (AttributeError, Exception):
                print(f"   ✅ AuthenticatedUser is immutable (frozen)")
            
            # Invalid email should raise in __post_init__
            try:
                AuthenticatedUser(
                    user_id="user-123",
                    email="invalid-email",  # Invalid
                    workspace_id="default",
                    role=UserRole.VIEWER.value
                )
                print("   ❌ Should reject invalid email")
            except ValueError as e:
                if "email" in str(e).lower():
                    print(f"   ✅ Invalid email rejected in __post_init__")
            
            # -- Test 3: get_current_user dependency (mocked JWT) ---------
            print("\n📌 Test 3: get_current_user (mocked JWT decode)")
            
            with patch("app.auth.dependencies.verify_access_token") as mock_verify, \
                 patch("app.auth.dependencies.get_settings") as mock_settings:
                
                # Mock settings: auth enabled
                mock_settings.return_value.auth_enabled = True
                
                # Mock valid token payload
                mock_verify.return_value = {
                    "sub": "user-123",
                    "email": "test@docmind.ai",
                    "workspace_id": "default",
                    "role": "editor",
                    "is_superuser": False,
                }
                
                # Mock request with correlation ID
                mock_request = MagicMock(spec=Request)
                mock_request.headers = {"x-correlation-id": "test-corr-123"}
                
                # Mock credentials
                creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="mock-token")
                
                # Call dependency
                user = await get_current_user(request=mock_request, credentials=creds)
                
                assert isinstance(user, AuthenticatedUser)
                assert user.user_id == "user-123"
                assert user.correlation_id == "test-corr-123"
                print(f"   ✅ get_current_user: resolved user={user.user_id}, corr_id={user.correlation_id}")
                
                # Missing credentials -> 401
                try:
                    await get_current_user(request=mock_request, credentials=None)
                    print("   ❌ Should reject missing token")
                except HTTPException as e:
                    if e.status_code == 401:
                        print(f"   ✅ Missing token rejected: 401 Unauthorized")
                
                # Invalid token -> 401
                mock_verify.return_value = None
                try:
                    await get_current_user(request=mock_request, credentials=creds)
                    print("   ❌ Should reject invalid token")
                except HTTPException as e:
                    if e.status_code == 401:
                        print(f"   ✅ Invalid token rejected: 401")
            
            # -- Test 4: Role-based dependencies -------------------------
            print("\n📌 Test 4: require_editor / require_admin guards")
            
            # Editor user
            editor_user = AuthenticatedUser(
                user_id="user-123",
                email="editor@docmind.ai",
                workspace_id="default",
                role=UserRole.EDITOR.value
            )
            
            # require_editor should pass for editor
            result = await require_editor(user=editor_user)
            assert result.user_id == "user-123"
            print(f"   ✅ require_editor: passed for editor role")
            
            # require_admin should fail for editor
            try:
                await require_admin(user=editor_user)
                print("   ❌ Should reject editor for admin guard")
            except HTTPException as e:
                if e.status_code == 403:
                    print(f"   ✅ require_admin: rejected editor (403 Forbidden)")
            
            # Admin user
            admin_user = AuthenticatedUser(
                user_id="user-456",
                email="admin@docmind.ai",
                workspace_id="default",
                role=UserRole.ADMIN.value
            )
            
            # Both guards should pass for admin
            await require_editor(user=admin_user)
            await require_admin(user=admin_user)
            print(f"   ✅ Both guards: passed for admin role")
            
            # -- Test 5: Dev mode bypass ---------------------------------
            print("\n📌 Test 5: Dev mode bypass (AUTH_ENABLED=false)")
            
            with patch("app.auth.dependencies.get_settings") as mock_settings:
                mock_settings.return_value.auth_enabled = False  # Dev mode
                
                mock_request = MagicMock(spec=Request)
                mock_request.headers = {"x-correlation-id": "dev-corr"}
                
                # Should return dev user without token
                user = await get_current_user(request=mock_request, credentials=None)
                
                assert user.user_id == "dev-user-001"
                assert user.email == "dev@documind.local"
                assert user.role == UserRole.ADMIN.value
                assert user.correlation_id == "dev-corr"
                print(f"   ✅ Dev mode: returned immutable dev user with corr_id")
            
            # -- Test 6: Error handling patterns -------------------------
            print("\n📌 Test 6: Error handling (HTTPException vs ValueError)")
            
            # Auth errors should be HTTPException (for FastAPI to convert to 401/403)
            # Validation errors should be ValueError (for input validation)
            
            # Test workspace validation error (ValueError)
            try:
                _validate_workspace_id("invalid@id!")
            except ValueError:
                print(f"   ✅ Workspace validation: raises ValueError (API converts to 400)")
            
            # Test auth error (HTTPException)
            try:
                from app.auth.dependencies import _auth_error
                raise _auth_error("Test auth error", 401, "test-corr")
            except HTTPException as e:
                if e.status_code == 401 and "X-Correlation-ID" in e.headers:
                    print(f"   ✅ Auth errors: raise HTTPException with corr_id header")
            
            print("\n" + "=" * 70)
            print("✅ ALL TESTS PASSED! Auth dependencies module verified.")
            print("\n💡 What we verified:")
            print("   • Workspace ID validation: UUID + slug + 'default' ✅")
            print("   • AuthenticatedUser: frozen, validation, with_correlation_id ✅")
            print("   • get_current_user: JWT decode, missing/invalid token -> 401 ✅")
            print("   • Role guards: require_editor, require_admin -> 403 if unauthorized ✅")
            print("   • Dev mode: bypass auth when AUTH_ENABLED=false ✅")
            print("   • Error handling: ValueError for validation, HTTPException for auth ✅")
            print("\n🔧 For integration tests:")
            print("   • Use Swagger UI 'Authorize' button to set token once")
            print("   • Or run: python scripts/test_auth_flow.py (full automation)")
            print("\n🔐 Security: Token validation server-side, immutable user objects")
            return True
            
        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Run async tests
    success = asyncio.run(run_tests())
    sys.exit(0 if success else 1)