"""
TOTP-based Multi-Factor Authentication endpoints.

Flow:
  1. POST /mfa/setup      — generate a new TOTP secret + provisioning URI (QR-ready)
  2. POST /mfa/verify     — confirm current TOTP code, then persist + enable MFA on the account
  3. POST /mfa/disable    — disable MFA (requires valid TOTP code as proof)

Dependencies:
  pip install pyotp qrcode[pil]   (qrcode is optional — clients can build the URI themselves)
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.database.session import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mfa", tags=["mfa"])

_TOTP_ISSUER = "DocMind AI"
_TOTP_DIGITS = 6
_TOTP_INTERVAL = 30  # seconds — RFC 6238 standard


# ── request/response schemas ──────────────────────────────────────────────────


class MFASetupResponse(BaseModel):
    secret: str = Field(description="Base-32 TOTP secret — store securely, show ONCE")
    provisioning_uri: str = Field(description="otpauth:// URI for QR-code generation")
    issuer: str


class MFATOTPRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=8, description="Current 6-digit TOTP code")


class MFAStatusResponse(BaseModel):
    mfa_enabled: bool


# ── helpers ───────────────────────────────────────────────────────────────────


def _require_pyotp():
    try:
        import pyotp  # noqa: PLC0415

        return pyotp
    except ImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MFA service unavailable: install pyotp (pip install pyotp)",
        ) from exc


def _verify_totp(secret: str, code: str) -> bool:
    pyotp = _require_pyotp()
    totp = pyotp.TOTP(secret, digits=_TOTP_DIGITS, interval=_TOTP_INTERVAL)
    return totp.verify(code, valid_window=1)  # ±1 window = ±30 s clock drift tolerance


# ── routes ────────────────────────────────────────────────────────────────────


@router.post("/setup", response_model=MFASetupResponse, summary="Generate TOTP secret for MFA enrollment")
async def mfa_setup(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    """
    Generate a new TOTP secret and return the provisioning URI.

    The secret is NOT saved yet — call POST /mfa/verify with a valid code to
    confirm the user can generate correct codes and persist MFA on the account.
    """
    pyotp = _require_pyotp()

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret, digits=_TOTP_DIGITS, interval=_TOTP_INTERVAL)
    uri = totp.provisioning_uri(name=user.email, issuer_name=_TOTP_ISSUER)

    # Stash the pending secret in DB so /verify can read and confirm it.
    # We set mfa_enabled=False here — /verify flips it to True.
    from sqlalchemy import update
    from app.auth.models import User

    await db.execute(
        update(User)
        .where(User.id == user.user_id)  # type: ignore[arg-type]
        .values(totp_secret=secret, mfa_enabled=False)
    )
    await db.commit()

    logger.info(f"MFA setup initiated for user {user.user_id}")
    return MFASetupResponse(secret=secret, provisioning_uri=uri, issuer=_TOTP_ISSUER)


@router.post("/verify", response_model=MFAStatusResponse, summary="Confirm TOTP code to activate MFA")
async def mfa_verify(
    body: MFATOTPRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    """
    Verify the TOTP code and activate MFA on the account.

    Must be called after /setup with the 6-digit code from the authenticator app.
    """
    from sqlalchemy import select, update
    from app.auth.models import User

    result = await db.execute(select(User).where(User.id == user.user_id))  # type: ignore[arg-type]
    db_user = result.scalar_one_or_none()
    if not db_user or not db_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA setup not started. Call POST /mfa/setup first.",
        )

    if not _verify_totp(db_user.totp_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid TOTP code. Check your authenticator app clock and try again.",
        )

    await db.execute(
        update(User)
        .where(User.id == user.user_id)  # type: ignore[arg-type]
        .values(mfa_enabled=True)
    )
    await db.commit()

    logger.info(f"MFA enabled for user {user.user_id}")
    return MFAStatusResponse(mfa_enabled=True)


@router.post("/disable", response_model=MFAStatusResponse, summary="Disable MFA (requires valid TOTP proof)")
async def mfa_disable(
    body: MFATOTPRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    """
    Disable MFA on the account.

    Requires a currently-valid TOTP code as proof of possession — prevents a
    stolen session cookie from silently stripping MFA protection.
    """
    from sqlalchemy import select, update
    from app.auth.models import User

    result = await db.execute(select(User).where(User.id == user.user_id))  # type: ignore[arg-type]
    db_user = result.scalar_one_or_none()
    if not db_user or not db_user.mfa_enabled or not db_user.totp_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is not currently enabled on this account.",
        )

    if not _verify_totp(db_user.totp_secret, body.code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid TOTP code. MFA was NOT disabled.",
        )

    await db.execute(
        update(User)
        .where(User.id == user.user_id)  # type: ignore[arg-type]
        .values(mfa_enabled=False, totp_secret=None)
    )
    await db.commit()

    logger.info(f"MFA disabled for user {user.user_id}")
    return MFAStatusResponse(mfa_enabled=False)


@router.get("/status", response_model=MFAStatusResponse, summary="Check MFA status for current user")
async def mfa_status(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_async_db)],
):
    from sqlalchemy import select
    from app.auth.models import User

    result = await db.execute(select(User.mfa_enabled).where(User.id == user.user_id))  # type: ignore[arg-type]
    row = result.first()
    enabled = bool(row[0]) if row else False
    return MFAStatusResponse(mfa_enabled=enabled)
