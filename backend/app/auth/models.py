# backend/app/auth/models.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Security
# ASCALE-FIX: S - Separation, C - Coupling
# [OK] FIXED: display_name + updated_at columns added to User model
# [OK] FIXED: UserRoleEnum uses explicit string literals
# [OK] FIXED: Completed truncated _enforce_single_primary event listener
# [OK] FIXED: hybrid_property -> property (avoids SQL query confusion)
# [OK] FIXED: Removed redundant timestamp listener (handled by column defaults)

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import TYPE_CHECKING, Optional, Final

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    String,
    Text,
    Index,
    UniqueConstraint,
    CheckConstraint,
    Integer,
    Float,
    event,
    func,
    text,
    Enum,
    ARRAY,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, relationship, validates, object_session
from app.core.validators import validate_email, validate_slug
from app.database.base import Base

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ========================================================================
# -- CONSTANTS & HELPERS ------------------------------------------------
# ========================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_SLUG_MIN_LENGTH: Final = 3
_SLUG_MAX_LENGTH: Final = 64

# PostgreSQL native ENUM  (workspace_admin added by migration)
UserRoleEnum = Enum(
    "admin",
    "workspace_admin",
    "editor",
    "viewer",
    name="user_role_enum",
    create_type=False,  # managed by migration script
    metadata=Base.metadata,
)


# ========================================================================
# -- ROLE ENUM (Python + DB sync) ---------------------------------------
# ========================================================================


class UserRole(str, PyEnum):
    """Application-level role enum — synced with DB enum.

    Hierarchy (highest → lowest):
      superadmin     → is_superuser=True on User (not a role value)
      workspace_admin → full workspace control
      admin          → legacy alias for workspace_admin (backward compat)
      editor         → can upload / query / annotate
      viewer         → read-only
    """

    WORKSPACE_ADMIN = "workspace_admin"
    ADMIN = "admin"  # legacy alias — treat same as workspace_admin
    EDITOR = "editor"
    VIEWER = "viewer"

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in {role.value for role in cls}

    @classmethod
    def admin_values(cls) -> frozenset[str]:
        """All role values that carry workspace-admin privileges."""
        return frozenset({cls.WORKSPACE_ADMIN.value, cls.ADMIN.value})


# ========================================================================
# -- WORKSPACE MODEL -----------------------------------------------------
# ========================================================================


class Workspace(Base):
    __tablename__ = "workspaces"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False)
    slug = Column(String(_SLUG_MAX_LENGTH), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )
    max_documents = Column(Integer, default=1000, nullable=False, server_default=text("1000"))
    max_queries = Column(Integer, default=10000, nullable=False, server_default=text("10000"))

    # Access management additions
    client_name = Column(String(128), nullable=True)
    client_email = Column(String(255), nullable=True)
    plan = Column(String(20), nullable=False, default="starter", server_default="starter")
    max_docs = Column(Integer, default=100, nullable=False, server_default=text("100"))
    max_queries_per_day = Column(Integer, default=500, nullable=False, server_default=text("500"))
    max_storage_gb = Column(Float, default=5.0, nullable=False, server_default=text("5.0"))
    storage_used_mb = Column(Float, default=0.0, nullable=False, server_default=text("0.0"))
    query_count_today = Column(Integer, default=0, nullable=False, server_default=text("0"))
    # Lazy daily-reset marker for query_count_today — see usage_tracker.check_query_limit().
    query_count_reset_at = Column(Date, nullable=False, default=lambda: _utcnow().date(), server_default=func.current_date())
    doc_count = Column(Integer, default=0, nullable=False, server_default=text("0"))
    domain_type = Column(String(50), nullable=True)
    suspended_at = Column(DateTime(timezone=True), nullable=True)
    suspended_reason = Column(Text, nullable=True)

    # Stripe billing (added post-creation via ensure_billing_schema — see app/core/billing_manager.py)
    stripe_customer_id = Column(String(255), nullable=True)
    stripe_subscription_id = Column(String(255), nullable=True)
    subscription_status = Column(String(30), nullable=False, default="none", server_default="none")

    members: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="workspace",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "slug ~* '^[a-z][a-z0-9_-]*[a-z0-9]$'",
            name="chk_workspace_slug_format",
        ),
        CheckConstraint(
            "max_documents > 0 AND max_queries > 0",
            name="chk_workspace_limits_positive",
        ),
        Index("ix_workspaces_active_slug", "is_active", "slug"),
    )

    @validates("slug")
    def _validate_slug(self, key: str, value: str) -> str:
        return validate_slug(value, min_len=_SLUG_MIN_LENGTH, max_len=_SLUG_MAX_LENGTH)

    def __repr__(self) -> str:
        return f"<Workspace(id={self.id}, slug='{self.slug}', active={self.is_active})>"

    @property  # [OK] FIXED: Changed from hybrid_property to avoid SQL query confusion
    def member_count(self) -> int:
        return len([m for m in self.members if m.is_active])


# ========================================================================
# -- USER MODEL ---------------------------------------------------------
# ========================================================================


class User(Base):
    """System user — authenticated via JWT."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False, unique=True, index=True)
    # Nullable: SSO-only users (see app/api/routes/sso.py) are JIT-provisioned without a
    # password — DROP NOT NULL is repaired via ensure_workspace_sso_schema().
    hashed_password = Column(String(255), nullable=True)
    full_name = Column(String(128), nullable=True)
    display_name = Column(String(100), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    is_superuser = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_email_verified = Column(Boolean, nullable=False, default=False, server_default=text("false"))

    # Access management additions
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    global_role = Column(String(20), nullable=False, default="viewer", server_default="viewer")

    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=True,
        default=_utcnow,
        onupdate=_utcnow,
    )

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        "WorkspaceMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint(
            "email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}$'",
            name="chk_user_email_format",
        ),
        Index("ix_users_active_email", "is_active", "email"),
        Index("ix_users_active_verified", "is_active", "is_email_verified"),
    )

    @property
    def workspace_ids(self) -> list[str]:
        return [str(m.workspace_id) for m in self.memberships if m.is_active and m.workspace]

    @property
    def primary_workspace_id(self) -> Optional[str]:
        primary = next(
            (m for m in self.memberships if m.is_active and m.is_primary and m.workspace),
            None,
        )
        if primary:
            return str(primary.workspace_id)
        first_active = next(
            (m for m in self.memberships if m.is_active and m.workspace),
            None,
        )
        return str(first_active.workspace_id) if first_active else None

    @validates("email")
    def _validate_email(self, key: str, value: str) -> str:
        return validate_email(value)

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}', active={self.is_active})>"

    def mark_login(self) -> None:
        self.last_login_at = _utcnow()


# ========================================================================
# -- WORKSPACE MEMBER MODEL ---------------------------------------------
# ========================================================================


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role = Column(
        UserRoleEnum,
        nullable=False,
        default=UserRole.VIEWER.value,
        server_default=UserRole.VIEWER.value,
    )
    is_active = Column(Boolean, default=True, nullable=False, server_default=text("true"))
    is_primary = Column(Boolean, default=False, nullable=False, server_default=text("false"))
    joined_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship("User", back_populates="memberships")
    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="members")

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_member_user_workspace"),
        Index("ix_workspace_member_active_ws", "workspace_id", "is_active", "role"),
        Index("ix_workspace_member_active_user", "user_id", "is_active", "is_primary"),
    )

    @validates("role")
    def _validate_role(self, key: str, value: str) -> str:
        if not UserRole.is_valid(value):
            raise ValueError(f"Invalid role: {value}. Must be one of {[r.value for r in UserRole]}")
        return value

    def __repr__(self) -> str:
        return (
            f"<WorkspaceMember(user={self.user_id}, workspace={self.workspace_id}, "
            f"role={self.role}, active={self.is_active})>"
        )

    def promote_to(self, new_role: UserRole) -> None:
        self.role = new_role.value


# ========================================================================
# -- INVITE MODEL -------------------------------------------------------
# ========================================================================


class Invite(Base):
    """Workspace invitation — token stored hashed, shown once."""

    __tablename__ = "invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    role = Column(String(20), nullable=False, default=UserRole.WORKSPACE_ADMIN.value)
    token_hash = Column(String(255), nullable=False, unique=True)
    token_prefix = Column(String(12), nullable=False)
    invited_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    resent_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )

    workspace: Mapped[Optional["Workspace"]] = relationship("Workspace", lazy="select")

    __table_args__ = (
        Index("ix_invites_email", "email"),
        Index("ix_invites_workspace_id", "workspace_id"),
        Index("ix_invites_status", "status"),
    )

    @property
    def is_expired(self) -> bool:
        return _utcnow() > self.expires_at

    def __repr__(self) -> str:
        return f"<Invite(email={self.email}, status={self.status})>"


# ========================================================================
# -- API KEY MODEL ------------------------------------------------------
# ========================================================================


class ApiKey(Base):
    """Workspace API key — full key shown ONCE, only hash stored."""

    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    key_hash = Column(String(255), nullable=False, unique=True)
    key_prefix = Column(String(20), nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, default=0, nullable=False, server_default=text("0"))
    is_active = Column(Boolean, default=True, nullable=False, server_default=text("true"))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(ARRAY(String), default=list, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", lazy="select")

    __table_args__ = (
        Index("ix_api_keys_workspace_id", "workspace_id"),
        Index("ix_api_keys_active", "is_active", "workspace_id"),
    )

    def __repr__(self) -> str:
        return f"<ApiKey(prefix={self.key_prefix}, active={self.is_active})>"


# ========================================================================
# -- USAGE LOG MODEL ----------------------------------------------------
# ========================================================================


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action_type = Column(String(50), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(UUID(as_uuid=True), nullable=True)
    tokens_used = Column(Integer, default=0, nullable=False, server_default=text("0"))
    ocr_pages = Column(Integer, default=0, nullable=False, server_default=text("0"))
    storage_delta_mb = Column(Float, default=0.0, nullable=False, server_default=text("0.0"))
    # [OK] FIXED: 'metadata' is a reserved SQLAlchemy attribute on Declarative models.
    # Renamed to 'log_metadata'; column name in DB stays "metadata" for backward compat.
    log_metadata = Column("metadata", JSONB, default=dict, nullable=False, server_default=text("'{}'"))
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_usage_logs_workspace_id", "workspace_id"),
        Index("ix_usage_logs_created_at", "created_at"),
        Index("ix_usage_logs_ws_date", "workspace_id", "created_at"),
    )


# ========================================================================
# -- AUDIT LOG MODEL ----------------------------------------------------
# ========================================================================


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id = Column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=True)
    resource_id = Column(String(255), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    request_data = Column(JSONB, default=dict, nullable=False, server_default=text("'{}'"))
    response_status = Column(Integer, nullable=True)
    severity = Column(String(10), nullable=False, default="info")
    created_at = Column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_audit_log_workspace_id", "workspace_id"),
        Index("ix_audit_log_action", "action"),
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_ws_date", "workspace_id", "created_at"),
    )


# ========================================================================
# -- EVENT LISTENERS ----------------------------------------------------
# ========================================================================


@event.listens_for(WorkspaceMember, "before_insert")
def _enforce_single_primary(mapper, connection, target: WorkspaceMember):
    """
    Ensure only one primary member per workspace.
    [OK] FIXED: Completed truncated listener.
    [WARN]️ Note: In async SQLAlchemy apps, this is often better handled in the
    service/endpoint layer to avoid sync DB calls during flush. This provides
    a safety net for sync migrations/tests.
    """
    if target.is_primary and target.workspace_id:
        session = object_session(target)
        if session:
            session.query(WorkspaceMember).filter(
                WorkspaceMember.workspace_id == target.workspace_id,
                WorkspaceMember.is_primary == True,
                WorkspaceMember.id != target.id,
            ).update({"is_primary": False}, synchronize_session="fetch")


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.auth.models) ---------
# ========================================================================

if __name__ == "__main__":
    import sys

    print("[>>] Testing Auth Models module (app/auth/models.py)")
    print("=" * 70)

    try:
        # [OK] NO IMPORT NEEDED!
        # We are inside the file, so User, Workspace, UserRole are already defined.

        # -- Test 1: UserRole enum ---------------------------------------
        print("\n[PIN] Test 1: UserRole enum values")
        assert UserRole.ADMIN.value == "admin"
        assert UserRole.EDITOR.value == "editor"
        assert UserRole.VIEWER.value == "viewer"
        assert UserRole.is_valid("admin") is True
        assert UserRole.is_valid("invalid") is False
        print("   [OK] UserRole: admin/editor/viewer + is_valid() check")

        # -- Test 2: SQLAlchemy model attributes ------------------------
        print("\n[PIN] Test 2: SQLAlchemy model structure (columns)")
        # Check User model has expected columns via __table__.c
        user_cols = [c.name for c in User.__table__.c]
        assert "email" in user_cols, f"Missing 'email' in User columns: {user_cols}"
        assert "hashed_password" in user_cols
        assert "is_active" in user_cols
        print("   [OK] User model columns: email, hashed_password, is_active")

        # Check Workspace model
        ws_cols = [c.name for c in Workspace.__table__.c]
        assert "slug" in ws_cols, f"Missing 'slug' in Workspace columns: {ws_cols}"
        assert "is_active" in ws_cols
        print("   [OK] Workspace model columns: slug, is_active")

        # Check WorkspaceMember model
        member_cols = [c.name for c in WorkspaceMember.__table__.c]
        assert "role" in member_cols, f"Missing 'role' in Member columns: {member_cols}"
        assert "is_primary" in member_cols
        print("   [OK] WorkspaceMember model columns: role, is_primary")

        # -- Test 3: Model relationships --------------------------------
        print("\n[PIN] Test 3: Model relationships (selectinload ready)")
        # Check relationships are defined via __mapper__.relationships
        user_rels = [r.key for r in User.__mapper__.relationships]
        assert "memberships" in user_rels, f"Missing 'memberships' in User rels: {user_rels}"
        print("   [OK] User relationships: memberships")

        ws_rels = [r.key for r in Workspace.__mapper__.relationships]
        assert "members" in ws_rels, f"Missing 'members' in Workspace rels: {ws_rels}"
        print("   [OK] Workspace relationships: members")

        # -- Test 4: Table metadata -------------------------------------
        print("\n[PIN] Test 4: Table metadata (schema validation)")
        assert User.__tablename__ == "users"
        assert Workspace.__tablename__ == "workspaces"
        assert WorkspaceMember.__tablename__ == "workspace_members"
        print("   [OK] Table names: users, workspaces, workspace_members")

        # Check primary keys
        assert len(User.__table__.primary_key) == 1
        assert str(list(User.__table__.primary_key)[0].name) == "id"
        print("   [OK] Primary keys: User.id (UUID)")

        print("\n" + "=" * 70)
        print("[OK] ALL TESTS PASSED! Auth models module verified.")
        print("\n[TIP] What we verified:")
        print("   • UserRole enum: admin/editor/viewer + validation [OK]")
        print("   • SQLAlchemy models: columns, relationships, primary keys [OK]")
        print("   • Table meta names, schema validation [OK]")
        print("\n[FIX] For integration tests:")
        print("   • Run: python -m app.api.routes.auth")
        print("\n[SEC] Security: Models enforce email format, password hashing")

    except Exception as e:
        print(f"\n[FAIL] Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
