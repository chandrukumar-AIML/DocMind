from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Final

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

_TOKEN_TYPE_ACCESS: Final = "access"
_TOKEN_TYPE_REFRESH: Final = "refresh"
_TOKEN_TYPE_SSO_STATE: Final = "sso_state"
_SSO_STATE_TTL_MINUTES: Final = 10
_BCRYPT_MAX_BYTES: Final = 72

# Redis key prefix for the JTI revocation blacklist
# Must match the key prefix used in app/api/routes/auth.py _revoke_access_token_blacklist
_REVOKE_PREFIX: Final = "revoked:access:"


def _get_redis() -> Optional[object]:
    """Return a sync Redis client for revocation checks, or None if unavailable."""
    try:
        import redis as _redis

        settings = get_settings()
        url = getattr(settings, "redis_url", None)
        if not url:
            return None
        return _redis.from_url(url, decode_responses=True, socket_connect_timeout=1)
    except Exception:
        return None


def revoke_token(jti: str, ttl_seconds: int) -> bool:
    """
    Blacklist a JTI in Redis so it cannot be reused even before expiry.

    Called on logout and on admin account disable. Returns True on success,
    False if Redis is unavailable (fail-open — token expires naturally).
    """
    r = _get_redis()
    if r is None:
        logger.warning("Token revocation skipped: Redis unavailable")
        return False
    try:
        r.setex(f"{_REVOKE_PREFIX}{jti}", ttl_seconds, "1")
        logger.info(f"Token JTI revoked: {jti[:8]}… (ttl={ttl_seconds}s)")
        return True
    except Exception as e:
        logger.warning(f"Token revocation failed: {e}")
        return False


def is_token_revoked(jti: str) -> bool:
    """Return True if the JTI is on the Redis blacklist."""
    r = _get_redis()
    if r is None:
        return False
    try:
        return bool(r.exists(f"{_REVOKE_PREFIX}{jti}"))
    except Exception:
        return False


# DVMELTSS-S: Private helpers — not exposed at module level
def _get_jwt_secret() -> str:
    """
    Fetch JWT secret from settings — FAIL FAST if missing.

    DVMELTSS-S: Critical — never allow missing secret in any environment.
    Use environment variables or secrets manager exclusively.
    """
    settings = get_settings()
    # instead of silently returning None when the attribute name is wrong
    secret = settings.jwt_secret_key if hasattr(settings, "jwt_secret_key") else None

    if not secret:
        env = getattr(settings, "environment", "dev")
        if env == "production":
            raise RuntimeError("CRITICAL: jwt_secret_key not configured for production")
        else:
            raise RuntimeError(
                "DEV SETUP REQUIRED: Set JWT_SECRET_KEY environment variable. "
                "For local dev only, you may use: export JWT_SECRET_KEY=$(openssl rand -hex 32)"
            )
    return secret


def _get_jwt_signing_key() -> str:
    """
    Return the key used to SIGN tokens.

    RS256 (asymmetric, production-preferred): private key from JWT_PRIVATE_KEY env var.
    HS256 (symmetric, dev/simple deployments): shared secret from JWT_SECRET_KEY.
    RS256 is selected automatically when JWT_PRIVATE_KEY is configured.
    """
    settings = get_settings()
    private_key = getattr(settings, "jwt_private_key", None)
    if private_key:
        return private_key.replace("\\n", "\n")
    return _get_jwt_secret()


def _get_jwt_verification_key() -> str:
    """
    Return the key used to VERIFY tokens.

    RS256: public key (can be distributed to every service without leaking signing ability).
    HS256: same shared secret as signing.
    """
    settings = get_settings()
    public_key = getattr(settings, "jwt_public_key", None)
    if public_key:
        return public_key.replace("\\n", "\n")
    return _get_jwt_secret()


def _get_jwt_algorithm() -> str:
    """
    Return RS256 when an RSA keypair is configured, HS256 otherwise.

    RS256 is strongly preferred for production — the private key never leaves the
    auth service, while every downstream service can verify tokens with only the
    public key (no shared-secret exposure).
    """
    settings = get_settings()
    if getattr(settings, "jwt_private_key", None):
        return "RS256"
    return getattr(settings, "jwt_algorithm", "HS256")


# -- Public API (DVMELTSS-M: Clear separation) -------------------------------


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    ✅ FIXED: REJECT passwords exceeding bcrypt's 72-byte limit (security fix).

    Args:
        password: Plaintext password string

    Returns:
        bcrypt-hashed password string

    Raises:
        ValueError: If password exceeds 72 bytes
    """
    password_bytes = password.encode("utf-8")

    # ✅ CRITICAL FIX: Reject long passwords instead of silent truncation
    if len(password_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password exceeds maximum length of {_BCRYPT_MAX_BYTES} bytes. "
            f"Current length: {len(password_bytes)} bytes. "
            "Please use a shorter password."
        )

    # modern bcrypt package versions during backend feature detection.
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """
    Verify a plaintext password against a bcrypt hash.

    Args:
        plain: Plaintext password to verify
        hashed: bcrypt-hashed password from database

    Returns:
        True if password matches, False otherwise

    Raises:
        ValueError: If plain password exceeds 72 bytes (consistent with hash_password)
    """
    plain_bytes = plain.encode("utf-8")

    # whose passwords were set before the 72-byte limit was enforced (bcrypt silently
    # truncated them). Returning False preserves backward compat: they simply fail auth
    # and are prompted to reset via the password-reset flow.
    if len(plain_bytes) > _BCRYPT_MAX_BYTES:
        logger.warning("verify_password: password exceeds 72 bytes — will not match any hash")
        return False

    if not hashed:
        return False

    try:
        return bcrypt.checkpw(plain_bytes, hashed.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        logger.warning("Password hash verification failed: %s", exc)
        return False


def create_access_token(
    user_id: str,
    email: str,
    workspace_id: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    family_id: Optional[str] = None,
) -> str:
    """
    Create a signed JWT access token.

    Claims include:
    - sub, email, workspace_id, role, exp, iat, type, jti, family_id

    Args:
        user_id: Unique user identifier
        email: User email address
        workspace_id: Active workspace ID
        role: User role (admin/editor/viewer)
        expires_delta: Optional custom expiry duration
        family_id: Optional token family ID for rotation tracking

    Returns:
        Signed JWT token string
    """
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=getattr(settings, "jwt_access_token_expire_minutes", 60))
    )
    payload = {
        "sub": user_id,
        "email": email,
        "workspace_id": workspace_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": _TOKEN_TYPE_ACCESS,
        "jti": str(uuid.uuid4()),  # Unique token ID for revocation
        "family_id": family_id or str(uuid.uuid4()),  # Lineage tracking
    }
    return jwt.encode(
        payload,
        _get_jwt_signing_key(),
        algorithm=_get_jwt_algorithm(),
    )


def create_refresh_token(
    user_id: str,
    workspace_id: str,
    family_id: Optional[str] = None,
) -> str:
    """
    Create a longer-lived refresh token for token rotation.

    Args:
        user_id: Unique user identifier
        workspace_id: User's workspace ID
        family_id: Optional token family ID to preserve lineage

    Returns:
        Signed JWT refresh token string
    """
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(days=getattr(settings, "jwt_refresh_token_expire_days", 30))

    # ✅ jti always generated — enables consistent replay detection
    jti = str(uuid.uuid4())

    payload = {
        "sub": user_id,
        "workspace_id": workspace_id,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": _TOKEN_TYPE_REFRESH,
        "family_id": family_id or str(uuid.uuid4()),
        "jti": jti,
    }

    return jwt.encode(
        payload,
        _get_jwt_signing_key(),
        algorithm=_get_jwt_algorithm(),
    )


def decode_token(
    token: str,
    expected_type: Optional[str] = None,
    token_type: Optional[str] = None,
) -> dict:
    """
    Decode and verify a JWT token.

    Args:
        token: JWT string to decode
        expected_type: If provided, validate token's 'type' claim matches
        token_type: Backward-compatible alias for expected_type

    Returns:
        Decoded payload dict

    Raises:
        JWTError: If token is invalid, expired, or type mismatch
    """
    payload = jwt.decode(
        token,
        _get_jwt_verification_key(),
        algorithms=[_get_jwt_algorithm()],
    )
    required_type = token_type or expected_type
    if required_type and payload.get("type") != required_type:
        raise JWTError(f"Token type mismatch: expected '{required_type}', got '{payload.get('type')}'")
    return payload


def verify_access_token(token: str) -> Optional[dict]:
    """
    Verify an access token and return its claims.

    Returns None if the token is invalid, expired, or has been explicitly
    revoked via revoke_token(). Caller is responsible for raising HTTPException.
    """
    try:
        payload = decode_token(token, expected_type=_TOKEN_TYPE_ACCESS)
    except JWTError as e:
        logger.debug(f"JWT access token verification failed: {e}")
        return None

    jti = payload.get("jti")
    if jti and is_token_revoked(jti):
        logger.warning(f"Rejected revoked token JTI: {jti[:8]}…")
        return None

    return payload


def create_sso_state_token(workspace_id: str, code_verifier: str, nonce: str) -> str:
    """
    Build a self-contained, tamper-proof OAuth2 `state` parameter for the OIDC
    authorization-code flow (see app/api/routes/sso.py).

    Carries everything the /sso/callback route needs (workspace_id, PKCE code_verifier,
    nonce) inside a short-lived signed JWT — no server-side session/Redis entry required
    between the authorize redirect and the IdP's callback.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=_SSO_STATE_TTL_MINUTES)
    payload = {
        "workspace_id": workspace_id,
        "code_verifier": code_verifier,
        "nonce": nonce,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": _TOKEN_TYPE_SSO_STATE,
    }
    return jwt.encode(payload, _get_jwt_signing_key(), algorithm=_get_jwt_algorithm())


def verify_sso_state_token(token: str) -> Optional[dict]:
    """Verify an SSO state token and return its claims. None if invalid/expired/tampered."""
    try:
        return decode_token(token, expected_type=_TOKEN_TYPE_SSO_STATE)
    except JWTError as e:
        logger.debug(f"SSO state token verification failed: {e}")
        return None


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.auth.jwt_handler) -----
# ========================================================================

