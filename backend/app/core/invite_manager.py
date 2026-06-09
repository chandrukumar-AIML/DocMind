# backend/app/core/invite_manager.py
"""
Invite manager — creates invite records, sends emails, validates tokens,
accepts invites (creates user + marks invite accepted).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.database.engine import async_engine
from app.config import get_settings

logger = logging.getLogger(__name__)

_INVITE_TTL_HOURS = 72
_TOKEN_BYTES = 32


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _make_token() -> tuple[str, str, str]:
    """Returns (raw_token, token_hash, token_prefix)."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    prefix = raw[:10]
    return raw, _hash_token(raw), prefix


# ── Schema bootstrap ─────────────────────────────────────────────────────────


async def ensure_invite_schema() -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS invites (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email        VARCHAR(255) NOT NULL,
                workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
                role         VARCHAR(20) NOT NULL DEFAULT 'workspace_admin',
                token_hash   VARCHAR(255) UNIQUE NOT NULL,
                token_prefix VARCHAR(12) NOT NULL,
                invited_by   UUID REFERENCES users(id) ON DELETE SET NULL,
                expires_at   TIMESTAMP WITH TIME ZONE NOT NULL,
                accepted_at  TIMESTAMP WITH TIME ZONE,
                resent_at    TIMESTAMP WITH TIME ZONE,
                status       VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_invites_email ON invites(email)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_invites_workspace_id ON invites(workspace_id)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_invites_status ON invites(status)"))


# ── Create invite ─────────────────────────────────────────────────────────────


async def create_invite(
    email: str,
    workspace_id: str,
    role: str,
    invited_by_user_id: str,
    ttl_hours: int = _INVITE_TTL_HOURS,
) -> tuple[str, dict[str, Any]]:
    """
    Insert invite row and return (raw_token, invite_dict).
    The raw_token is shown ONCE — only the hash is stored.
    """
    raw, token_hash, token_prefix = _make_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)

    async with async_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            INSERT INTO invites
                (email, workspace_id, role, token_hash, token_prefix,
                 invited_by, expires_at, status)
            VALUES
                (:email, :workspace_id, :role, :token_hash, :token_prefix,
                 :invited_by, :expires_at, 'pending')
            ON CONFLICT (token_hash) DO NOTHING
            RETURNING id::text, email, workspace_id::text, role,
                      token_prefix, expires_at, status, created_at
        """),
                    {
                        "email": email.lower(),
                        "workspace_id": workspace_id,
                        "role": role,
                        "token_hash": token_hash,
                        "token_prefix": token_prefix,
                        "invited_by": invited_by_user_id,
                        "expires_at": expires_at,
                    },
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        # Hash collision (astronomically unlikely) — retry once
        raw, token_hash, token_prefix = _make_token()
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        async with async_engine.begin() as conn:
            row = (
                (
                    await conn.execute(
                        text("""
                INSERT INTO invites
                    (email, workspace_id, role, token_hash, token_prefix,
                     invited_by, expires_at, status)
                VALUES
                    (:email, :workspace_id, :role, :token_hash, :token_prefix,
                     :invited_by, :expires_at, 'pending')
                RETURNING id::text, email, workspace_id::text, role,
                          token_prefix, expires_at, status, created_at
            """),
                        {
                            "email": email.lower(),
                            "workspace_id": workspace_id,
                            "role": role,
                            "token_hash": token_hash,
                            "token_prefix": token_prefix,
                            "invited_by": invited_by_user_id,
                            "expires_at": expires_at,
                        },
                    )
                )
                .mappings()
                .fetchone()
            )

    return raw, dict(row)


# ── Validate token ────────────────────────────────────────────────────────────


async def validate_invite_token(raw_token: str) -> dict[str, Any] | None:
    """
    Validate token and return invite info (including workspace_name).
    Returns None if invalid/expired.
    """
    token_hash = _hash_token(raw_token)
    async with async_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text("""
            SELECT i.id::text AS invite_id,
                   i.email, i.role, i.expires_at, i.status,
                   w.name AS workspace_name,
                   w.id::text AS workspace_id,
                   u.full_name AS inviter_name
            FROM invites i
            LEFT JOIN workspaces w ON w.id = i.workspace_id
            LEFT JOIN users u ON u.id = i.invited_by
            WHERE i.token_hash = :hash
        """),
                    {"hash": token_hash},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        return None
    if row["status"] != "pending":
        return None
    if datetime.now(timezone.utc) > row["expires_at"]:
        await _mark_expired(token_hash)
        return None
    return dict(row)


async def _mark_expired(token_hash: str) -> None:
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            UPDATE invites SET status = 'expired'
            WHERE token_hash = :hash AND status = 'pending'
        """),
            {"hash": token_hash},
        )


# ── Accept invite ─────────────────────────────────────────────────────────────


async def accept_invite(
    raw_token: str,
    full_name: str,
    password: str,
) -> dict[str, Any]:
    """
    Accept invite: create user, create workspace_member, mark invite accepted.
    Returns JWT tokens.
    """
    from app.auth.jwt_handler import (
        hash_password,
        create_access_token,
        create_refresh_token,
    )

    invite = await validate_invite_token(raw_token)
    if not invite:
        raise ValueError("Invalid or expired invite token")

    hashed_pw = hash_password(password)
    token_hash = _hash_token(raw_token)

    async with async_engine.begin() as conn:
        # Create or get user
        existing = (
            (
                await conn.execute(
                    text("""
            SELECT id::text AS user_id, email FROM users WHERE email = :email
        """),
                    {"email": invite["email"]},
                )
            )
            .mappings()
            .fetchone()
        )

        if existing:
            user_id = existing["user_id"]
            await conn.execute(
                text("""
                UPDATE users SET full_name = :name, hashed_password = :pw,
                                 is_active = TRUE, global_role = :role
                WHERE id = :uid
            """),
                {
                    "name": full_name,
                    "pw": hashed_pw,
                    "role": invite["role"],
                    "uid": user_id,
                },
            )
        else:
            user_row = (
                (
                    await conn.execute(
                        text("""
                INSERT INTO users (email, full_name, hashed_password, is_active,
                                   is_email_verified, global_role)
                VALUES (:email, :name, :pw, TRUE, TRUE, :role)
                RETURNING id::text AS user_id
            """),
                        {
                            "email": invite["email"],
                            "name": full_name,
                            "pw": hashed_pw,
                            "role": invite["role"],
                        },
                    )
                )
                .mappings()
                .fetchone()
            )
            user_id = user_row["user_id"]

        # Add workspace membership
        if invite["workspace_id"]:
            await conn.execute(
                text("""
                INSERT INTO workspace_members (user_id, workspace_id, role, is_active, is_primary)
                VALUES (:uid, :wsid, :role, TRUE, TRUE)
                ON CONFLICT (user_id, workspace_id)
                DO UPDATE SET role = EXCLUDED.role, is_active = TRUE
            """),
                {
                    "uid": user_id,
                    "wsid": invite["workspace_id"],
                    "role": invite["role"],
                },
            )

        # Mark invite accepted
        await conn.execute(
            text("""
            UPDATE invites SET status = 'accepted', accepted_at = NOW()
            WHERE token_hash = :hash
        """),
            {"hash": token_hash},
        )

    access = create_access_token(
        user_id=user_id,
        email=invite["email"],
        workspace_id=invite["workspace_id"] or "default",
        role=invite["role"],
    )
    refresh = create_refresh_token(user_id=user_id, workspace_id=invite["workspace_id"] or "default")

    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "user_id": user_id,
        "email": invite["email"],
        "role": invite["role"],
        "workspace_id": invite["workspace_id"],
        "workspace_name": invite["workspace_name"],
    }


# ── Resend invite ─────────────────────────────────────────────────────────────


async def resend_invite(invite_id: str, invited_by_user_id: str) -> tuple[str, dict[str, Any]]:
    """Revoke old token, issue fresh one."""
    async with async_engine.connect() as conn:
        old = (
            (
                await conn.execute(
                    text("""
            SELECT email, workspace_id::text, role FROM invites
            WHERE id = :id AND status = 'pending'
        """),
                    {"id": invite_id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not old:
        raise ValueError("Invite not found or already accepted/expired")

    # Expire old
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            UPDATE invites SET status = 'expired', resent_at = NOW()
            WHERE id = :id
        """),
            {"id": invite_id},
        )

    return await create_invite(
        email=old["email"],
        workspace_id=old["workspace_id"],
        role=old["role"],
        invited_by_user_id=invited_by_user_id,
    )


# ── List invites ──────────────────────────────────────────────────────────────


async def list_invites(workspace_id: str) -> list[dict[str, Any]]:
    async with async_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
            SELECT i.id::text AS invite_id, i.email, i.role,
                   i.token_prefix, i.expires_at, i.accepted_at,
                   i.status, i.created_at,
                   u.full_name AS invited_by_name
            FROM invites i
            LEFT JOIN users u ON u.id = i.invited_by
            WHERE i.workspace_id = :wsid
            ORDER BY i.created_at DESC
        """),
                    {"wsid": workspace_id},
                )
            )
            .mappings()
            .fetchall()
        )
        return [dict(r) for r in rows]


# ── Email sending ─────────────────────────────────────────────────────────────


async def send_invite_email(
    to_email: str,
    workspace_name: str,
    inviter_name: str,
    raw_token: str,
    role: str,
) -> bool:
    """Send invite email via SMTP. Returns True on success."""
    settings = get_settings()
    smtp_host = getattr(settings, "smtp_host", None)
    if not smtp_host:
        logger.info(f"SMTP not configured — invite link for {to_email}: token={raw_token[:8]}…")
        return False

    base_url = getattr(settings, "invite_base_url", "https://app.documind.ai")
    invite_url = f"{base_url}/invite/{raw_token}"
    subject = "You've been invited to DocuMind AI"
    body = (
        f"Hi,\n\n"
        f"{inviter_name} has invited you to join the '{workspace_name}' workspace on DocuMind AI "
        f"as a {role}.\n\n"
        f"Click the link below to set your password and get started:\n"
        f"{invite_url}\n\n"
        f"This link expires in 72 hours.\n\n"
        f"If you did not expect this invitation, you can safely ignore this email.\n\n"
        f"— The DocuMind AI Team"
    )

    try:
        import aiosmtplib
        from email.mime.text import MIMEText

        msg = MIMEText(body, "plain")
        msg["Subject"] = subject
        msg["From"] = getattr(settings, "smtp_from", "noreply@documind.ai")
        msg["To"] = to_email

        port = int(getattr(settings, "smtp_port", 587))
        use_tls = port == 465

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=port,
            start_tls=(not use_tls) and (port == 587),
            use_tls=use_tls,
            username=getattr(settings, "smtp_user", None),
            password=getattr(settings, "smtp_password", None),
        )
        logger.info(f"Invite email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send invite email to {to_email}: {e}")
        return False


# ── Onboarding progress ───────────────────────────────────────────────────────


async def get_onboarding_progress(workspace_id: str) -> dict[str, bool]:
    async with async_engine.connect() as conn:
        doc_count = (
            await conn.execute(
                text("""
            SELECT COALESCE(doc_count, 0) FROM workspaces WHERE id = :wsid
        """),
                {"wsid": workspace_id},
            )
        ).scalar() or 0

        query_count = (
            await conn.execute(
                text("""
            SELECT COUNT(*) FROM usage_logs
            WHERE workspace_id = :wsid
              AND action_type IN ('query_executed', 'agent_query', 'graph_query')
        """),
                {"wsid": workspace_id},
            )
        ).scalar() or 0

        key_count = (
            await conn.execute(
                text("""
            SELECT COUNT(*) FROM api_keys
            WHERE workspace_id = :wsid AND is_active = TRUE
        """),
                {"wsid": workspace_id},
            )
        ).scalar() or 0

        member_count = (
            await conn.execute(
                text("""
            SELECT COUNT(*) FROM workspace_members
            WHERE workspace_id = :wsid AND is_active = TRUE
        """),
                {"wsid": workspace_id},
            )
        ).scalar() or 0

    return {
        "workspace_created": True,
        "first_member_added": member_count >= 1,
        "first_doc_uploaded": doc_count >= 1,
        "first_query_run": query_count >= 1,
        "api_key_created": key_count >= 1,
    }


if __name__ == "__main__":
    import asyncio

    async def _test():
        print("Testing invite_manager…")
        await ensure_invite_schema()
        print("  Schema ensured.")
        # Test token generation
        raw, token_hash, prefix = _make_token()
        assert len(raw) > 20
        assert _hash_token(raw) == token_hash
        print(f"  Token OK: prefix={prefix}")
        print("Done.")

    asyncio.run(_test())
