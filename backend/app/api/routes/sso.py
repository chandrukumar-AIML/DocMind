"""
SSO login via OIDC — per-workspace identity provider config, authorization-code flow with
PKCE, and JIT (just-in-time) user provisioning on first login.

Works with any standards-compliant OIDC issuer (Okta, Azure AD/Entra ID, Google Workspace,
Auth0, OneLogin, ...) — no provider-specific branching, just `{issuer}/.well-known/
openid-configuration` discovery.
"""

from __future__ import annotations

import logging
import secrets

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin
from app.auth.jwt_handler import create_sso_state_token, verify_sso_state_token
from app.auth.models import User as UserModel, UserRole, Workspace as WorkspaceModel, WorkspaceMember
from app.config import get_settings
from app.core.ids import generate_correlation_id
from app.core.workspace_sso_config import (
    delete_workspace_sso_config,
    get_workspace_sso_config,
    get_workspace_sso_config_by_slug,
    get_workspace_sso_config_masked,
    upsert_workspace_sso_config,
)
from app.database.session import get_async_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sso", tags=["sso"])

_DISCOVERY_TIMEOUT = 10.0


class SsoConfigRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=255)
    client_secret: str = Field(..., min_length=1, max_length=500)
    issuer: str = Field(..., min_length=1, max_length=500)


async def _discover(issuer: str) -> dict:
    """Fetch the IdP's OIDC discovery document."""
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        logger.error(f"OIDC discovery failed for issuer {issuer}: {e}")
        raise HTTPException(status_code=502, detail=f"Could not reach identity provider: {e}")


@router.get("/config")
async def get_sso_config(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    config = await get_workspace_sso_config_masked(user.workspace_id)
    if config is None:
        return {"configured": False}
    return {"configured": True, **config}


@router.put("/config")
async def update_sso_config(
    body: SsoConfigRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    result = await upsert_workspace_sso_config(
        workspace_id=user.workspace_id,
        client_id=body.client_id,
        client_secret=body.client_secret,
        issuer=body.issuer,
    )
    return {"configured": True, **result}


@router.delete("/config")
async def clear_sso_config(
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> dict:
    deleted = await delete_workspace_sso_config(user.workspace_id)
    return {"deleted": deleted}


@router.get("/authorize")
async def authorize(workspace_slug: str) -> RedirectResponse:
    """Entry point from the login screen — redirects the browser to the IdP."""
    config = await get_workspace_sso_config_by_slug(workspace_slug)
    if config is None:
        raise HTTPException(status_code=404, detail=f"No SSO configured for workspace '{workspace_slug}'")

    discovery = await _discover(config.issuer)
    authorization_endpoint = discovery.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(status_code=502, detail="Identity provider discovery is missing authorization_endpoint")

    settings = get_settings()
    nonce = secrets.token_urlsafe(24)
    code_verifier = secrets.token_urlsafe(48)
    # Self-contained signed state — computed before the auth URL so it can be passed
    # straight into create_authorization_url() instead of Authlib's own random state,
    # avoiding server-side session storage between the authorize and callback steps.
    signed_state = create_sso_state_token(
        workspace_id=config.workspace_id,
        code_verifier=code_verifier,
        nonce=nonce,
    )
    # Callback must point back at THIS backend (the IdP redirects here, not to the frontend).
    callback_url = f"http://{settings.api_host}:{settings.api_port}/api/v1/sso/callback"

    client = AsyncOAuth2Client(client_id=config.client_id, redirect_uri=callback_url)
    try:
        auth_url, _ = client.create_authorization_url(
            authorization_endpoint,
            state=signed_state,
            scope="openid email profile",
            nonce=nonce,
            code_verifier=code_verifier,
        )
    finally:
        await client.aclose()

    return RedirectResponse(url=auth_url, status_code=302)


@router.get("/callback")
async def callback(code: str, state: str, db=Depends(get_async_db)) -> RedirectResponse:
    """IdP redirects here after the user authenticates. Exchanges code, provisions the
    user, and issues our own JWT exactly like password login does."""
    corr_id = generate_correlation_id("sso_callback")
    claims = verify_sso_state_token(state)
    if claims is None:
        raise HTTPException(status_code=400, detail="Invalid or expired SSO state")

    workspace_id = claims["workspace_id"]
    code_verifier = claims["code_verifier"]

    config = await get_workspace_sso_config(workspace_id)
    if config is None:
        raise HTTPException(status_code=404, detail="SSO configuration no longer exists for this workspace")

    discovery = await _discover(config.issuer)
    token_endpoint = discovery.get("token_endpoint")
    userinfo_endpoint = discovery.get("userinfo_endpoint")
    if not token_endpoint or not userinfo_endpoint:
        raise HTTPException(status_code=502, detail="Identity provider discovery is missing required endpoints")

    settings = get_settings()
    callback_url = f"http://{settings.api_host}:{settings.api_port}/api/v1/sso/callback"

    client = AsyncOAuth2Client(client_id=config.client_id, client_secret=config.client_secret, redirect_uri=callback_url)
    try:
        await client.fetch_token(token_endpoint, code=code, code_verifier=code_verifier)
        userinfo_response = await client.get(userinfo_endpoint)
        userinfo_response.raise_for_status()
        userinfo = userinfo_response.json()
    except httpx.HTTPError as e:
        logger.error(f"[{corr_id}] OIDC token exchange/userinfo failed: {e}")
        raise HTTPException(status_code=502, detail=f"Identity provider login failed: {e}")
    finally:
        await client.aclose()

    email = (userinfo.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(status_code=502, detail="Identity provider did not return an email address")

    # Find-or-create the User (JIT provisioning) — mirrors app/api/routes/auth.py's
    # register() flow, minus password (SSO-only users have hashed_password=None).
    existing = (await db.execute(select(UserModel).where(UserModel.email == email))).scalar_one_or_none()
    if existing is None:
        existing = UserModel(
            email=email,
            hashed_password=None,
            display_name=userinfo.get("name") or email,
            is_active=True,
            is_email_verified=True,
        )
        db.add(existing)
        await db.flush()

    membership = (
        await db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == existing.id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        membership = WorkspaceMember(
            user_id=existing.id,
            workspace_id=workspace_id,
            role=UserRole.EDITOR.value,
            is_primary=True,
            is_active=True,
        )
        db.add(membership)
        role = UserRole.EDITOR.value
    else:
        role = membership.role

    await db.commit()
    await db.refresh(existing)

    from app.api.routes.auth import _create_token_response, _set_auth_cookies

    access_ttl = getattr(settings, "jwt_access_token_expire_minutes", 60) * 60
    token_resp = _create_token_response(
        user=existing,
        access_ttl_seconds=access_ttl,
        correlation_id=corr_id,
        workspace_id=str(workspace_id),
        role=role,
    )

    redirect = RedirectResponse(url=f"{settings.frontend_url.rstrip('/')}/?sso=success", status_code=302)
    _set_auth_cookies(redirect, token_resp, access_ttl)
    logger.info(f"[{corr_id}] SSO login succeeded: user={existing.id} workspace={workspace_id}")
    return redirect


__all__ = ["router"]
