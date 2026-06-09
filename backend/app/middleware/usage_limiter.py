# backend/app/middleware/usage_limiter.py
"""
Usage limiter middleware — checks workspace limits before uploads/queries.
Also supports API key authentication in Authorization header.
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Routes that trigger document limit check
_UPLOAD_PATHS = {"/api/v1/ingest", "/api/v1/documents/upload"}

# Routes that trigger query limit check
_QUERY_PATHS = {"/api/v1/query", "/api/v1/agent/query", "/api/v1/graph/query"}


class UsageLimiterMiddleware(BaseHTTPMiddleware):
    """
    Before upload routes: check doc_count < max_docs and storage < max_storage_gb.
    Before query routes:  check query_count_today < max_queries_per_day.
    Returns HTTP 429 with descriptive message if limit exceeded.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        path = request.url.path
        method = request.method

        # Only apply on POST/PUT (writes)
        if method not in ("POST", "PUT"):
            return await call_next(request)

        workspace_id = _extract_workspace_id(request)
        if not workspace_id:
            return await call_next(request)

        try:
            if any(path.startswith(p) for p in _UPLOAD_PATHS):
                from app.core.usage_tracker import check_doc_limit, check_storage_limit

                ok, msg = await check_doc_limit(workspace_id)
                if not ok:
                    return _limit_response(msg)
                # Storage check — estimate 10MB if unknown (full check in endpoint)
                ok, msg = await check_storage_limit(workspace_id, incoming_mb=0.1)
                if not ok:
                    return _limit_response(msg)

            elif any(path.startswith(p) for p in _QUERY_PATHS):
                from app.core.usage_tracker import check_query_limit

                ok, msg = await check_query_limit(workspace_id)
                if not ok:
                    return _limit_response(msg)

        except Exception as e:
            logger.warning(f"[UsageLimiter] Check failed (non-blocking): {e}")

        return await call_next(request)


def _extract_workspace_id(request: Request) -> str | None:
    """Try to get workspace_id from JWT claims or API key validation."""
    # Check request.state if auth middleware already set it
    if hasattr(request.state, "workspace_id"):
        return request.state.workspace_id

    # Try to decode JWT without full validation (for middleware speed)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from app.auth.jwt_handler import verify_access_token

            payload = verify_access_token(auth.split(" ", 1)[1])
            if payload:
                return payload.get("workspace_id")
        except Exception:
            pass
    elif auth.startswith("ApiKey "):
        # API key — workspace resolved in ApiKeyAuthMiddleware first
        if hasattr(request.state, "api_key_workspace_id"):
            return request.state.api_key_workspace_id

    return None


def _limit_response(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "error": "workspace_limit_exceeded",
            "detail": message,
            "upgrade_url": "https://documind.ai/pricing",
        },
        headers={"Retry-After": "86400"},
    )


class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    Recognizes `Authorization: ApiKey dmk_...` header.
    Validates the key and injects workspace_id into request.state.
    JWT-based auth is unaffected — this only activates on ApiKey scheme.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("ApiKey "):
            return await call_next(request)

        raw_key = auth.split(" ", 1)[1].strip()
        try:
            from app.core.apikey_manager import validate_api_key

            ctx = await validate_api_key(raw_key)
            if not ctx:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "error": "invalid_api_key",
                        "detail": "API key is invalid, inactive, or expired",
                    },
                )
            request.state.api_key_workspace_id = ctx["workspace_id"]
            request.state.api_key_scopes = ctx["scopes"]
            request.state.workspace_id = ctx["workspace_id"]
        except Exception as e:
            logger.error(f"[ApiKeyAuth] Validation error: {e}")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "api_key_validation_error"},
            )

        return await call_next(request)
