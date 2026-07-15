from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional, Final, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
    BackgroundTasks,
    Query,
)
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    field_validator,
    model_validator,
    ConfigDict,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.jwt_handler import (
    hash_password,
    verify_password,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.auth.models import (
    User as UserModel,
    WorkspaceMember,
    UserRole,
    Workspace as WorkspaceModel,
)
from app.database.session import get_async_db
from app.monitoring.metrics_collector import record_auth_attempt
from app.cache import get_cache

logger = logging.getLogger(__name__)

# -- Constants --------------------------------------------------------------
# Read from settings so JWT_ACCESS_TOKEN_EXPIRE_MINUTES env var is respected
ACCESS_TOKEN_TTL_MINUTES: int = getattr(settings, "jwt_access_token_expire_minutes", 60)
ACCESS_TOKEN_TTL_REMEMBER_MINUTES: int = max(ACCESS_TOKEN_TTL_MINUTES, 60)
REFRESH_TOKEN_TTL_DAYS: Final = 30
PASSWORD_MIN_LENGTH: Final = 12
BCRYPT_MAX_LENGTH: Final = 72
_CACHE_TIMEOUT: Final = float(getattr(settings, "auth_cache_timeout_seconds", 1.0))

router = APIRouter(prefix="/auth", tags=["auth"])


# -- Response Models --------------------------------------------------------
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    email: str
    workspace_id: str
    role: str
    correlation_id: Optional[str] = None
    email_verified: bool = True


class RegistrationPendingResponse(BaseModel):
    message: str
    user_id: str
    email: str
    workspace_id: str
    workspace_slug: str
    correlation_id: str


class ProfileResponse(BaseModel):
    user_id: str
    email: str
    email_verified: bool
    display_name: Optional[str] = None
    workspace_id: str
    role: str
    updated_at: str
    correlation_id: str


# -- Request Models ---------------------------------------------------------
class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, max_length=BCRYPT_MAX_LENGTH)
    remember_me: bool = Field(default=False)
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "remember_me": True,
            }
        }
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=20, max_length=2048)
    model_config = ConfigDict(
        json_schema_extra={"example": {"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}}
    )

    @field_validator("refresh_token")
    @classmethod
    def validate_token_format(cls, v: str) -> str:
        if v.count(".") != 2:
            raise ValueError("Invalid JWT format")
        return v


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=BCRYPT_MAX_LENGTH)
    invite_token: Optional[str] = Field(default=None)
    display_name: Optional[str] = Field(default=None, max_length=100)
    # Optional — self-registration creates a new isolated workspace named after this
    # (see register() below); if omitted, a default name is derived.
    workspace_name: Optional[str] = Field(default=None, max_length=128)
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "user@example.com",
                "password": "SecurePass123!",
                "display_name": "John",
            }
        }
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain a number")
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', v):
            raise ValueError("Password must contain a special character")
        return v


class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=BCRYPT_MAX_LENGTH)
    current_password: Optional[str] = Field(default=None)
    display_name: Optional[str] = Field(default=None, max_length=100)
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "display_name": "Updated Name",
                "current_password": "OldPass123!",
            }
        }
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[str]) -> Optional[str]:
        return v.lower().strip() if v else v

    @model_validator(mode="after")
    def validate_sensitive_changes(self) -> "UserUpdate":
        if (self.email is not None or self.password is not None) and not self.current_password:
            raise ValueError("Current password required to change email or password")
        return self


# -- Helper Functions -------------------------------------------------------
def _validate_auth_inputs(
    email: Optional[str],
    password: Optional[str],
    workspace_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate auth endpoint inputs."""
    if email is not None and (not isinstance(email, str) or len(email.strip()) < 3):
        return False, "email must be a valid string with at least 3 characters"
    if password is not None and (not isinstance(password, str) or len(password) < 8):
        return False, "password must be at least 8 characters"
    return True, ""


async def _check_login_rate_limit(cache, identifier: str, window_seconds: int = 900, max_attempts: int = 5) -> bool:
    try:
        key = f"rate:login:{identifier}"
        attempts = await asyncio.wait_for(cache.incr(key, expire_seconds=window_seconds), timeout=_CACHE_TIMEOUT)
        return attempts <= max_attempts
    except Exception as e:
        logger.warning(f"Rate limit check failed (fail-open): {e}")
        return True


async def _check_registration_rate_limit(cache, email: str, ip: str) -> bool:
    try:
        domain = email.split("@")[-1].lower()
        domain_count = await asyncio.wait_for(
            cache.incr(f"rate:register:domain:{domain}", expire_seconds=3600),
            timeout=_CACHE_TIMEOUT,
        )
        ip_count = await asyncio.wait_for(
            cache.incr(f"rate:register:ip:{ip}", expire_seconds=3600),
            timeout=_CACHE_TIMEOUT,
        )
        return domain_count <= 10 and ip_count <= 5
    except Exception as e:
        logger.warning(f"Registration rate limit failed (fail-open): {e}")
        return True


async def _revoke_refresh_token(cache, user_id: str, token: str) -> bool:
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key = f"refresh:{user_id}:{token_hash}"
        return await asyncio.wait_for(cache.delete(key), timeout=_CACHE_TIMEOUT) > 0
    except Exception as e:
        logger.warning(f"Failed to revoke refresh token: {e}")
        return False


async def _store_refresh_token(
    cache,
    user_id: str,
    token: str,
    family_id: str,
    ttl_days: int = REFRESH_TOKEN_TTL_DAYS,
):
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key = f"refresh:{user_id}:{token_hash}"
        await asyncio.wait_for(
            cache.setex(
                key,
                ttl_days * 86400,
                json.dumps({"family_id": family_id, "issued_at": time.time()}),
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except Exception as e:
        logger.warning(f"Failed to store refresh token: {e}")


async def _revoke_access_token_blacklist(cache, jti: str, user_id: str, expires_at: float):
    try:
        key = f"revoked:access:{jti}"
        ttl_seconds = max(0, int(expires_at - datetime.now(timezone.utc).timestamp()))
        if ttl_seconds > 0:
            await asyncio.wait_for(
                cache.setex(
                    key,
                    ttl_seconds,
                    json.dumps(
                        {
                            "user_id": user_id,
                            "revoked_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ),
                ),
                timeout=_CACHE_TIMEOUT,
            )
    except Exception as e:
        logger.warning(f"Failed to blacklist access token: {e}")


async def _revoke_token_family(cache, user_id: str, family_id: str):
    try:
        await asyncio.wait_for(
            cache.setex(
                f"revoked:family:{user_id}:{family_id}",
                REFRESH_TOKEN_TTL_DAYS * 86400,
                "1",
            ),
            timeout=_CACHE_TIMEOUT,
        )
    except Exception as e:
        logger.warning(f"Failed to revoke token family: {e}")


def _generate_token_family_id() -> str:
    return f"fam_{uuid.uuid4().hex[:16]}"


def _set_auth_cookies(response: Response, token_resp: "TokenResponse", access_ttl: int) -> None:
    """
    [OK] FIXED: Set JWT tokens as httpOnly cookies (XSS-safe).

    httpOnly=True  — JavaScript cannot read these cookies (XSS-proof).
    secure=True    — cookies only sent over HTTPS (production).
    samesite="lax" — protects against CSRF while allowing same-site GET redirects.

    Tokens are ALSO returned in the response body for backward compatibility
    with Swagger UI, API clients, and mobile apps that use Bearer header auth.
    """
    is_secure = getattr(settings, "cookie_secure", True)
    cookie_domain = getattr(settings, "cookie_domain", None) or None

    response.set_cookie(
        key="access_token",
        value=token_resp.access_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=access_ttl,
        path="/",
        domain=cookie_domain,
    )
    # Refresh token scoped to refresh endpoint only — leaked cookie is less useful
    response.set_cookie(
        key="refresh_token",
        value=token_resp.refresh_token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_TTL_DAYS * 86400,
        path="/api/v1/auth/refresh",
        domain=cookie_domain,
    )


def _create_token_response(
    user: UserModel,
    access_ttl_seconds: int,
    correlation_id: str,
    workspace_id: str,
    role: str,
    email_verified: bool = True,
    family_id: Optional[str] = None,
) -> TokenResponse:
    """Generate JWT access + refresh tokens and return a TokenResponse."""
    actual_family_id = family_id or _generate_token_family_id()
    access_token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        workspace_id=workspace_id,
        role=role,
        expires_delta=timedelta(seconds=access_ttl_seconds),
        family_id=actual_family_id,
    )
    refresh_token = create_refresh_token(user_id=str(user.id), workspace_id=workspace_id, family_id=actual_family_id)
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=access_ttl_seconds,
        user_id=str(user.id),
        email=user.email,
        workspace_id=workspace_id,
        role=role,
        correlation_id=correlation_id,
        email_verified=email_verified,
    )


def _create_email_verification_token(user_id: str, email: str, ttl_hours: int = 24) -> str:
    from app.auth.jwt_handler import jwt, _get_jwt_secret, _get_jwt_algorithm

    payload = {
        "sub": str(user_id),
        "email": email,
        "purpose": "email_verification",
        "exp": datetime.now(timezone.utc) + timedelta(hours=ttl_hours),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=_get_jwt_algorithm())


async def _send_verification_email(to_email: str, token: str, correlation_id: str):
    verification_link = f"{getattr(settings, 'frontend_url', 'http://localhost:3000')}/verify-email?token={token}"
    logger.info(f"[{correlation_id}] Verification email to {to_email}: {verification_link}")


async def _authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[tuple[UserModel, str, str]]:
    """Verify credentials and return (user, workspace_id, role) if valid."""
    try:
        stmt = (
            select(UserModel)
            .options(selectinload(UserModel.memberships).selectinload(WorkspaceMember.workspace))
            .where(UserModel.email == email.lower().strip())
        )
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        workspace_id = user.primary_workspace_id or "default"
        role = UserRole.VIEWER.value
        if user.memberships:
            primary = next(
                (m for m in user.memberships if m.is_active and m.is_primary and m.workspace),
                None,
            )
            if primary:
                role = primary.role
            else:
                first_active = next((m for m in user.memberships if m.is_active and m.workspace), None)
                if first_active:
                    role = first_active.role
        return user, workspace_id, role
    except Exception as e:
        logger.error(f"Authentication error: {e}", exc_info=True)
        return None


# -- ENDPOINTS --------------------------------------------------------------
# 1️⃣ POST /register
@router.post(
    "/register",
    response_model=RegistrationPendingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
)
async def register(
    request: Request,
    body: UserCreate,
    db: AsyncSession = Depends(get_async_db),
    background_tasks: BackgroundTasks = None,
):
    if not getattr(settings, "allow_self_registration", True):
        raise HTTPException(status_code=403, detail="Self-registration is disabled")
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("register")
    cache = await get_cache()
    client_ip = request.client.host if request.client else "unknown"
    if not await _check_registration_rate_limit(cache, body.email, client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many registration attempts. Please try later.",
            headers={"X-Correlation-ID": corr_id},
        )
    try:
        stmt = select(UserModel).where(UserModel.email == body.email.lower().strip())
        if (await db.execute(stmt)).scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Email already registered")
        is_dev = getattr(settings, "environment", "dev") == "dev" or getattr(settings, "skip_email_verification", False)
        hashed_password = hash_password(body.password)
        new_user = UserModel(
            email=body.email.lower().strip(),
            hashed_password=hashed_password,
            display_name=body.display_name,
            is_active=is_dev,
            is_email_verified=is_dev,
        )
        db.add(new_user)
        await db.flush()

        # ✅ Self-serve multi-tenant signup: each registration gets its OWN isolated
        # workspace (not a shared "default" one — two different companies signing up
        # must never land in the same workspace). Storage (Chroma/Neo4j/BM25) is left
        # to lazy-init on first ingest, matching every other workspace in this app —
        # no synchronous provisioning call needed here.
        ws_display_name = (
            body.workspace_name.strip()
            if body.workspace_name and body.workspace_name.strip()
            else f"{(new_user.display_name or body.email.split('@')[0]).strip()}'s Workspace"
        )
        slug_base = re.sub(r"[^a-z0-9]+", "-", ws_display_name.lower()).strip("-") or "workspace"
        ws_slug = f"{slug_base[:40]}-{uuid.uuid4().hex[:6]}"

        new_workspace = WorkspaceModel(name=ws_display_name, slug=ws_slug, is_active=True)
        db.add(new_workspace)
        await db.flush()

        member = WorkspaceMember(
            user_id=new_user.id,
            workspace_id=new_workspace.id,
            role=UserRole.WORKSPACE_ADMIN,
            is_primary=True,
            is_active=True,
        )
        db.add(member)
        workspace_id = str(new_workspace.id)

        await db.commit()
        await db.refresh(new_user)

        # Fire welcome email as background task (non-blocking; silently skips if SMTP not set)
        if background_tasks:
            from app.core.invite_manager import send_welcome_email
            background_tasks.add_task(
                send_welcome_email,
                to_email=new_user.email,
                display_name=new_user.display_name or "",
                workspace_name=ws_display_name,
            )

        if not is_dev:
            verification_token = _create_email_verification_token(new_user.id, new_user.email)
            if background_tasks:
                background_tasks.add_task(
                    _send_verification_email,
                    to_email=new_user.email,
                    token=verification_token,
                    correlation_id=corr_id,
                )
            logger.info(f"[{corr_id}] User registered pending verification: id={new_user.id}")
            return RegistrationPendingResponse(
                message="Registration successful. Please check your email to verify your account.",
                user_id=str(new_user.id),
                email=new_user.email,
                workspace_id=workspace_id,
                workspace_slug=ws_slug,
                correlation_id=corr_id,
            )
        logger.info(f"[{corr_id}] User registered and auto-verified (dev mode): id={new_user.id}")
        return RegistrationPendingResponse(
            message="Registration successful. Account auto-verified in dev mode.",
            user_id=str(new_user.id),
            email=new_user.email,
            workspace_id=workspace_id,
            workspace_slug=ws_slug,
            correlation_id=corr_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"[{corr_id}] Registration failed: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration service temporarily unavailable",
            headers={"X-Correlation-ID": corr_id},
        )


# 2️⃣ POST /verify-email
@router.post(
    "/verify-email",
    response_model=TokenResponse,
    summary="Verify email and activate account",
)
async def verify_email(
    request: Request,
    token: str = Query(..., description="Email verification token"),
    db: AsyncSession = Depends(get_async_db),
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("verify_email")
    try:
        payload = decode_token(token)
        if payload.get("purpose") != "email_verification":
            raise ValueError("Invalid token purpose")
        user_id = payload.get("sub")
        email = payload.get("email")
        stmt = select(UserModel).where(UserModel.id == user_id, UserModel.email == email)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user or user.is_email_verified:
            raise HTTPException(status_code=400, detail="Invalid or expired verification token")
        user.is_active = True
        user.is_email_verified = True
        await db.commit()
        workspace_id = user.primary_workspace_id or "default"
        role = UserRole.VIEWER.value
        if user.memberships:
            primary = next(
                (m for m in user.memberships if m.is_active and m.is_primary and m.workspace),
                None,
            )
            if primary:
                role = primary.role
            else:
                first_active = next((m for m in user.memberships if m.is_active and m.workspace), None)
                if first_active:
                    role = first_active.role
        logger.info(f"[{corr_id}] Email verified and account activated: user={user.id}")
        return _create_token_response(
            user,
            ACCESS_TOKEN_TTL_MINUTES * 60,
            corr_id,
            workspace_id,
            role,
            email_verified=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[{corr_id}] Email verification failed: {e}")
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired verification token",
            headers={"X-Correlation-ID": corr_id},
        )


# 3️⃣ POST /login
@router.post("/login", response_model=TokenResponse, summary="User login")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_async_db),
):
    # Tokens returned in body for backward compat (Swagger/API clients) AND set
    # as httpOnly cookies for browser clients (XSS-safe).
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("login")
    is_valid, error = _validate_auth_inputs(body.email, body.password, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    cache = await get_cache()
    if not await _check_login_rate_limit(cache, body.email):
        record_auth_attempt(
            workspace_id="unknown",
            correlation_id=corr_id,
            success=False,
            user_id=None,
            auth_method="password",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
            headers={"X-Correlation-ID": corr_id, "Retry-After": "900"},
        )
    try:
        user_result = await _authenticate_user(db, body.email, body.password)
        if not user_result:
            record_auth_attempt(
                workspace_id="unknown",
                correlation_id=corr_id,
                success=False,
                user_id=None,
                auth_method="password",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"X-Correlation-ID": corr_id},
            )
        user, workspace_id, role = user_result
        access_ttl = ACCESS_TOKEN_TTL_REMEMBER_MINUTES * 60 if body.remember_me else ACCESS_TOKEN_TTL_MINUTES * 60
        record_auth_attempt(
            workspace_id=workspace_id,
            correlation_id=corr_id,
            success=True,
            user_id=str(user.id),
            auth_method="password",
        )
        logger.info(f"[{corr_id}] Login success: user={user.id} email={user.email}")
        token_resp = _create_token_response(
            user,
            access_ttl,
            corr_id,
            workspace_id,
            role,
            email_verified=getattr(user, "is_email_verified", True),
        )
        _set_auth_cookies(response, token_resp, access_ttl)  # [OK] FIXED: httpOnly cookies
        return token_resp
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Login endpoint failed: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication service temporarily unavailable",
            headers={"X-Correlation-ID": corr_id},
        )


# 4️⃣ POST /refresh
@router.post("/refresh", response_model=TokenResponse, summary="Refresh access token")
async def refresh_token_endpoint(
    request: Request,
    response: Response,
    body: RefreshRequest,
    db: AsyncSession = Depends(get_async_db),
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("refresh")
    cache = await get_cache()
    try:
        payload = decode_token(body.refresh_token, token_type="refresh")
        user_id = payload.get("sub")
        workspace_id = payload.get("workspace_id")
        old_jti = payload.get("jti")
        family_id = payload.get("family_id")
        if not user_id or not workspace_id:
            raise ValueError("Invalid token payload — missing sub or workspace_id")
        if old_jti:
            try:
                is_revoked = await asyncio.wait_for(cache.exists(f"revoked:refresh:{old_jti}"), timeout=_CACHE_TIMEOUT)
                if is_revoked:
                    if family_id:
                        await _revoke_token_family(cache, user_id, family_id)
                    logger.warning(f"[{corr_id}] Refresh token reuse detected: user={user_id}")
                    raise HTTPException(
                        status_code=401,
                        detail="Token revoked or reused",
                        headers={"X-Correlation-ID": corr_id},
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.debug(f"Cache check for revoked token failed (proceeding): {e}")
        stmt = select(UserModel).where(UserModel.id == user_id, UserModel.is_active == True)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=401, detail="User not found or disabled")
        role = UserRole.VIEWER.value
        if hasattr(user, "memberships") and user.memberships:
            membership = next(
                (m for m in user.memberships if str(m.workspace_id) == workspace_id and m.is_active),
                None,
            )
            if membership:
                role = membership.role
        if old_jti:
            try:
                await asyncio.wait_for(
                    cache.setex(
                        f"revoked:refresh:{old_jti}",
                        REFRESH_TOKEN_TTL_DAYS * 86400,
                        "1",
                    ),
                    timeout=_CACHE_TIMEOUT,
                )
            except Exception:
                pass
        await _revoke_refresh_token(cache, user_id, body.refresh_token)
        access_ttl = ACCESS_TOKEN_TTL_MINUTES * 60
        token_resp = _create_token_response(
            user,
            access_ttl,
            corr_id,
            workspace_id,
            role,
            email_verified=getattr(user, "is_email_verified", True),
            family_id=family_id,
        )
        _set_auth_cookies(response, token_resp, access_ttl)  # [OK] FIXED: rotate cookies
        return token_resp
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[{corr_id}] Token refresh failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"X-Correlation-ID": corr_id},
        )


# 5️⃣ GET /me
@router.get("/me", response_model=ProfileResponse, summary="Get current user profile")
async def get_current_user_profile(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    stmt = select(UserModel).where(UserModel.id == user.user_id)
    result = await db.execute(stmt)
    db_user = result.scalar_one_or_none()
    now_iso = datetime.now(timezone.utc).isoformat()
    return ProfileResponse(
        user_id=user.user_id,
        email=user.email,
        email_verified=getattr(db_user, "is_email_verified", True) if db_user else True,
        display_name=getattr(db_user, "display_name", None) if db_user else None,
        workspace_id=user.workspace_id,
        role=user.role,
        updated_at=(db_user.updated_at.isoformat() if db_user and db_user.updated_at else now_iso),
        correlation_id=user.correlation_id,
    )


# 6️⃣ PUT /me
@router.put("/me", response_model=ProfileResponse, summary="Update user profile")
async def update_user_profile(
    request: Request,
    body: UserUpdate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
    background_tasks: BackgroundTasks = None,
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("update_profile")
    try:
        stmt = select(UserModel).where(UserModel.id == user.user_id)
        result = await db.execute(stmt)
        db_user = result.scalar_one_or_none()
        if not db_user:
            raise HTTPException(status_code=404, detail="User not found")
        if body.email is not None or body.password is not None:
            if not body.current_password or not verify_password(body.current_password, db_user.hashed_password):
                raise HTTPException(status_code=403, detail="Current password is incorrect")
        if body.email is not None and body.email.lower().strip() != db_user.email:
            email_lower = body.email.lower().strip()
            stmt_check = select(UserModel).where(UserModel.email == email_lower)
            if (await db.execute(stmt_check)).scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Email already in use")
            db_user.email = email_lower
            db_user.is_email_verified = False
            if not getattr(settings, "skip_email_verification", False) and background_tasks:
                verification_token = _create_email_verification_token(db_user.id, db_user.email)
                background_tasks.add_task(
                    _send_verification_email,
                    to_email=db_user.email,
                    token=verification_token,
                    correlation_id=corr_id,
                )
                logger.info(f"[{corr_id}] Email changed, verification sent: user={user.user_id}")
        if body.password is not None:
            db_user.hashed_password = hash_password(body.password)
            logger.info(f"[{corr_id}] Password changed: user={user.user_id}")
        if body.display_name is not None:
            db_user.display_name = body.display_name.strip()
        db_user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"[{corr_id}] Profile updated: user={user.user_id[:8]}...")
        return ProfileResponse(
            user_id=str(db_user.id),
            email=db_user.email,
            email_verified=db_user.is_email_verified,
            display_name=db_user.display_name,
            workspace_id=user.workspace_id,
            role=user.role,
            updated_at=db_user.updated_at.isoformat() if db_user.updated_at else datetime.now(timezone.utc).isoformat(),
            correlation_id=corr_id,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Profile update failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Profile update service unavailable",
            headers={"X-Correlation-ID": corr_id},
        )


# 7️⃣ POST /logout
@router.post("/logout", status_code=204, summary="Logout (invalidate token)")
async def logout(
    request: Request,
    response: Response,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_async_db),
):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("logout")
    cache = await get_cache()
    try:
        # Revoke access token (from cookie or header)
        token = request.cookies.get("access_token") or (
            request.headers.get("Authorization", "")[7:]
            if request.headers.get("Authorization", "").startswith("Bearer ")
            else None
        )
        if token:
            try:
                payload = decode_token(token, token_type="access")
                jti = payload.get("jti")
                exp = payload.get("exp")
                if jti and exp:
                    await _revoke_access_token_blacklist(cache, jti, user.user_id, exp)
            except Exception:
                pass

        # Revoke refresh token (from cookie or body)
        refresh_token = request.cookies.get("refresh_token")
        if not refresh_token:
            try:
                body_bytes = await request.body()
                if body_bytes:
                    body_json = await request.json()
                    refresh_token = body_json.get("refresh_token")
            except Exception:
                pass
        if refresh_token:
            await _revoke_refresh_token(cache, user.user_id, refresh_token)

        is_secure = getattr(settings, "cookie_secure", True)
        cookie_domain = getattr(settings, "cookie_domain", None) or None
        response.delete_cookie(
            "access_token",
            path="/",
            domain=cookie_domain,
            secure=is_secure,
            httponly=True,
            samesite="lax",
        )
        response.delete_cookie(
            "refresh_token",
            path="/api/v1/auth/refresh",
            domain=cookie_domain,
            secure=is_secure,
            httponly=True,
            samesite="lax",
        )

        logger.info(f"[{corr_id}] Logout: user={user.user_id[:8]}... (cookies cleared)")
    except Exception as e:
        logger.error(f"[{corr_id}] Logout error: {e}", exc_info=True)
    return None


# 8️⃣ POST /logout/all
@router.post("/logout/all", status_code=204, summary="Logout from all devices")
async def logout_all(request: Request, user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
    corr_id = request.headers.get("X-Correlation-ID") or generate_correlation_id("logout_all")
    cache = await get_cache()
    try:
        pattern = f"refresh:{user.user_id}:*"
        keys = await asyncio.wait_for(cache.keys(pattern), timeout=_CACHE_TIMEOUT)
        if keys:
            await asyncio.wait_for(cache.delete(*keys), timeout=_CACHE_TIMEOUT)
        logger.info(f"[{corr_id}] All sessions revoked: user={user.user_id[:8]}...")
    except Exception as e:
        logger.error(f"[{corr_id}] Logout all error: {e}", exc_info=True)
    return None


# 9️⃣ POST /token — OAuth2 / Swagger UI compatibility
@router.post("/token", include_in_schema=False)
async def oauth2_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_async_db),
):
    """OAuth2-compatible token endpoint for Swagger UI Authorize button."""
    credentials = LoginRequest(email=form_data.username, password=form_data.password, remember_me=False)
    return await login(request, credentials, db)


# ── API Key Management (in-memory store, survives process lifetime) ──────
_api_keys: dict[str, dict] = {}  # key_id -> {name, token, user_id, created_at}


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Label for the API key")
    expires_days: int = Field(default=365, ge=1, le=3650)


@router.post("/api-keys", summary="Generate a long-lived API key")
async def create_api_key(
    body: ApiKeyCreate,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    key_id = str(uuid.uuid4())[:8]
    token = create_access_token(
        user_id=user.user_id,
        email=user.email,
        workspace_id=user.workspace_id,
        role=user.role,
        expires_delta=timedelta(days=body.expires_days),
    )
    entry = {
        "key_id": key_id,
        "name": body.name,
        "token": token,
        "user_id": user.user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_days": body.expires_days,
    }
    _api_keys[key_id] = entry
    return entry


@router.get("/api-keys", summary="List API keys for current user")
async def list_api_keys(user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
    return {
        "keys": [
            {
                "key_id": v["key_id"],
                "name": v["name"],
                "created_at": v["created_at"],
                "expires_days": v["expires_days"],
            }
            for v in _api_keys.values()
            if v["user_id"] == user.user_id
        ]
    }


@router.delete("/api-keys/{key_id}", status_code=204, summary="Revoke an API key")
async def delete_api_key(key_id: str, user: Annotated[AuthenticatedUser, Depends(get_current_user)]):
    entry = _api_keys.get(key_id)
    if not entry or entry["user_id"] != user.user_id:
        raise HTTPException(status_code=404, detail="API key not found")
    del _api_keys[key_id]


def get_auth_metadata() -> dict[str, Any]:
    """✅ NEW: Return auth endpoint metadata for debugging."""
    return {
        "endpoints": [
            "/register",
            "/verify-email",
            "/login",
            "/refresh",
            "/me",
            "/logout",
            "/logout/all",
            "/token",
        ],
        "token_ttl": {
            "access_minutes": ACCESS_TOKEN_TTL_MINUTES,
            "access_remember_minutes": ACCESS_TOKEN_TTL_REMEMBER_MINUTES,
            "refresh_days": REFRESH_TOKEN_TTL_DAYS,
        },
        "password_requirements": {
            "min_length": PASSWORD_MIN_LENGTH,
            "max_length": BCRYPT_MAX_LENGTH,
            "requires_uppercase": True,
            "requires_lowercase": True,
            "requires_digit": True,
            "requires_special": True,
        },
        "cache_timeout_seconds": _CACHE_TIMEOUT,
        "rate_limits": {
            "login": {"window_seconds": 900, "max_attempts": 5},
            "registration": {"domain_limit": 10, "ip_limit": 5, "window_seconds": 3600},
        },
    }


__all__ = ["router", "get_auth_metadata"]

# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.api.routes.auth) -----
# ========================================================================

