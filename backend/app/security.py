# backend/app/security.py
# DVMELTSS-FIX: S - Security, V - Validate, E - Error handling
# ASCALE-FIX: S - Separation, C - Coupling, E - Error propagation
# OWASP-FIX: 3 - Credential safety, 7 - Safe data handling
from __future__ import annotations
import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional, Final
from fastapi import Security, HTTPException, status, Request
from fastapi.security import APIKeyHeader, APIKeyQuery, HTTPBearer

from app.config import get_settings

logger = logging.getLogger(__name__)

# -- Authentication Schemes ------------------------------------
API_KEY_NAME: Final = "X-API-Key"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)
api_key_query = APIKeyQuery(name="api_key", auto_error=False)
http_bearer = HTTPBearer(auto_error=False)


# -- API Key Validation (Production-Ready) ---------------------
def validate_api_key(
    key: Optional[str] = Security(api_key_header),
    query_key: Optional[str] = Security(api_key_query),
    required: bool = True,
) -> Optional[str]:
    """
    Validate API key from header or query param.

    Args:
        key: API key from X-API-Key header
        query_key: API key from ?api_key= query param
        required: If False, returns None instead of raising

    Returns:
        Validated API key string or None

    Raises:
        HTTPException: If key is missing/invalid and required=True
    """
    settings = get_settings()

    # Prefer header over query param (more secure)
    api_key = key or query_key

    if not api_key:
        if required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Missing API key. Provide via {API_KEY_NAME} header.",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return None

    # ✅ FIXED: Production validation against configured app API keys
    if not settings.api_reload:
        # In production, validate against app-specific API keys.
        # FIXED: Only application API keys authorize this API. Provider keys
        # such as OPENAI_API_KEY must never grant access to the service.
        valid_keys = [key.strip() for key in settings.app_api_keys if key and key.strip()]
        if not valid_keys:
            logger.error("No API keys configured for production API validation")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server misconfigured: no API keys available for validation",
            )
        if not any(hmac.compare_digest(api_key, valid_key) for valid_key in valid_keys):
            logger.warning("Invalid API key attempt")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    return api_key


# -- JWT/Token Utilities --------------------------------------
def create_secure_token(length: int = 32) -> str:
    """
    Generate a cryptographically secure random token.

    Args:
        length: Token length in bytes (default 32 = 256 bits)

    Returns:
        Hex-encoded secure token string
    """
    return secrets.token_hex(length)


def verify_hmac_signature(
    payload: bytes,
    signature: str,
    secret: bytes,
    algorithm: str = "sha256",
) -> bool:
    """
    Verify HMAC signature for webhook security (constant-time compare).

    Args:
        payload: Raw request body bytes
        signature: Hex-encoded signature from request header
        secret: Shared secret for HMAC
        algorithm: Hash algorithm (default: sha256)

    Returns:
        True if signature is valid, False otherwise
    """
    try:
        expected = hmac.new(
            secret,
            payload,
            getattr(hashlib, algorithm),
        ).hexdigest()
        # ✅ FIXED: Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected.lower(), signature.lower())
    except Exception as e:
        logger.warning(f"HMAC verification failed: {e}")
        return False


# -- Production Rate Limiter (Redis-backed) -------------------
class RateLimiter:
    """
    Redis-backed rate limiter for production use.

    Falls back to in-memory for development.
    """

    def __init__(self, max_requests: int, window_seconds: int, redis_client=None):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis = redis_client
        self._memory_store: dict[str, list[float]] = {} if redis_client is None else None

    def is_allowed(self, key: str) -> bool:
        """
        Check if request is allowed under rate limit.

        Args:
            key: Unique identifier (e.g., API key, IP address)

        Returns:
            True if request is allowed, False if rate limited
        """
        now = time.time()
        window_start = now - self.window_seconds

        if self._redis:
            # Redis implementation (atomic)
            pipe = self._redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {now: now})
            pipe.zcard(key)
            pipe.expire(key, self.window_seconds + 1)
            _, _, count, _ = pipe.execute()
            if count > self.max_requests:
                return False
            return True
        else:
            # In-memory fallback (development only)
            self._memory_store[key] = [ts for ts in self._memory_store.get(key, []) if ts > window_start]
            if len(self._memory_store[key]) >= self.max_requests:
                return False
            self._memory_store[key].append(now)
            return True

    def get_retry_after(self, key: str) -> Optional[int]:
        """Get seconds until rate limit resets."""
        if self._memory_store and key in self._memory_store:
            oldest = min(self._memory_store[key])
            return max(0, int(oldest + self.window_seconds - time.time()))
        return None


# -- Security Headers Middleware ------------------------------
async def add_security_headers(request: Request, call_next):
    """
    Add security headers to all responses (OWASP best practices).

    Headers added:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Strict-Transport-Security: max-age=31536000 (prod only)
    - Content-Security-Policy: default-src 'self'
    """
    settings = get_settings()

    response = await call_next(request)

    # Core security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"

    if settings.api_reload:
        # Development: permissive CSP to allow Swagger UI (CDN scripts/styles/images)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.jsdelivr.net; "
            "font-src 'self' data:; "
            "connect-src 'self';"
        )
    else:
        # Production: strict CSP + HSTS + deny framing
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

    return response


# -- Correlation ID Middleware (Distributed Tracing) ----------
async def add_correlation_id(request: Request, call_next):
    """
    Inject correlation_id into request context and response headers.

    Enables end-to-end tracing across microservices.
    """
    from app.core.ids import generate_correlation_id

    # Get or generate correlation ID
    corr_id = (
        request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID") or generate_correlation_id("api")
    )

    # Attach to request state for logging
    request.state.correlation_id = corr_id

    # Process request
    response = await call_next(request)

    # Add to response headers
    response.headers["X-Correlation-ID"] = corr_id

    return response


# Export for easy importing
__all__ = [
    "validate_api_key",
    "create_secure_token",
    "verify_hmac_signature",
    "RateLimiter",
    "add_security_headers",
    "add_correlation_id",
    "API_KEY_NAME",
    "api_key_header",
    "api_key_query",
    "http_bearer",
]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
