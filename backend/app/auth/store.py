
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.validators import (
    validate_email,
    validate_slug,
    validate_password_strength,
)
from .models import User, Workspace, WorkspaceMember, UserRole
from .jwt_handler import hash_password, verify_password
from app.database.engine import AsyncSessionLocal

logger = logging.getLogger(__name__)


def _generate_safe_slug_suffix() -> str:
    return secrets.token_hex(4)


class UserStore:
    """Async CRUD for users, workspaces, and memberships."""

    def __init__(self) -> None:
        self._session_factory = AsyncSessionLocal

    # -- USER OPERATIONS -------------------------------------------------------

    async def create_user(
        self,
        email: str,
        password: str,
        full_name: str = "",
        workspace_id: Optional[str] = None,
        role: UserRole = UserRole.EDITOR,
    ) -> User:
        """Create a user and optionally add to a workspace."""
        # DVMELTSS-V: Validate inputs early (BEFORE DB session)
        email = validate_email(email)
        is_valid, pwd_error = validate_password_strength(password)
        if not is_valid:
            raise ValueError(f"Password validation failed: {pwd_error}")

        # bcrypt is CPU-bound and will freeze the event loop if run inside async context
        hashed_pw = hash_password(password)

        async with self._session_factory() as session:
            async with session.begin():
                # Check email uniqueness
                existing = await session.scalar(select(User).where(User.email == email))
                if existing:
                    raise ValueError(f"Email already registered: {email}")

                user = User(
                    email=email,
                    hashed_password=hashed_pw,  # ✅ Use pre-hashed value
                    full_name=full_name.strip() or None,
                )
                session.add(user)
                await session.flush()  # Generate user.id

                # Create personal workspace if none specified
                target_ws_id = workspace_id
                if not target_ws_id:
                    base = (full_name or email).split("@")[0]
                    base = base.lower().replace(".", "_").replace(" ", "_")[:20]
                    suffix = _generate_safe_slug_suffix()
                    slug = validate_slug(f"{base}_{suffix}")

                    workspace = Workspace(
                        name=f"{full_name or email}'s Workspace",
                        slug=slug,
                    )
                    session.add(workspace)
                    await session.flush()
                    target_ws_id = str(workspace.id)
                else:
                    ws_uuid = uuid.UUID(target_ws_id)
                    ws = await session.scalar(select(Workspace).where(Workspace.id == ws_uuid))
                    if not ws:
                        raise ValueError(f"Workspace not found: {target_ws_id}")
                    if not ws.is_active:
                        raise ValueError("Target workspace is inactive")

                    existing_member = await session.scalar(
                        select(WorkspaceMember).where(
                            WorkspaceMember.user_id == user.id,
                            WorkspaceMember.workspace_id == ws_uuid,
                        )
                    )
                    if existing_member:
                        raise ValueError("User is already a member of this workspace")

                # Create membership
                member = WorkspaceMember(
                    user_id=user.id,
                    workspace_id=uuid.UUID(target_ws_id),
                    role=role,
                    is_primary=True,
                    is_active=True,
                )
                session.add(member)

        logger.info(f"User created: {email} | workspace={target_ws_id}")
        return user

    async def authenticate(self, email: str, password: str) -> Optional[tuple[User, str]]:
        """Authenticate user with email + password."""
        email = validate_email(email)
        async with self._session_factory() as session:
            user = await session.scalar(
                select(User)
                .options(selectinload(User.memberships).selectinload(WorkspaceMember.workspace))
                .where(User.email == email)
            )
            if not user or not user.is_active:
                return None
            if not verify_password(password, user.hashed_password):
                return None

            # Update last login
            user.last_login_at = datetime.now(timezone.utc)
            await session.commit()

            return user, user.primary_workspace_id or "default"

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        async with self._session_factory() as session:
            return await session.scalar(
                select(User)
                .options(selectinload(User.memberships).selectinload(WorkspaceMember.workspace))
                .where(User.id == uuid.UUID(user_id))
            )

    async def get_user_workspaces(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active workspaces a user has access to."""
        async with self._session_factory() as session:
            stmt = (
                select(WorkspaceMember, Workspace)
                .join(Workspace, WorkspaceMember.workspace_id == Workspace.id)
                .where(
                    WorkspaceMember.user_id == uuid.UUID(user_id),
                    WorkspaceMember.is_active == True,
                    Workspace.is_active == True,
                )
                .order_by(WorkspaceMember.is_primary.desc(), Workspace.created_at.desc())
            )
            result = await session.execute(stmt)
            return [
                {
                    "workspace_id": str(ws.id),
                    "name": ws.name,
                    "slug": ws.slug,
                    "role": member.role.value,
                    "is_primary": member.is_primary,
                }
                for member, ws in result
            ]

    # -- WORKSPACE OPERATIONS --------------------------------------------------

    async def create_workspace(
        self,
        name: str,
        slug: str,
        description: str = "",
        created_by: Optional[str] = None,
    ) -> Workspace:
        """Create a new workspace and optionally add creator as admin."""
        slug = validate_slug(slug)

        async with self._session_factory() as session:
            async with session.begin():
                existing = await session.scalar(select(Workspace).where(Workspace.slug == slug))
                if existing:
                    raise ValueError(f"Workspace slug already taken: {slug}")

                workspace = Workspace(
                    name=name.strip(),
                    slug=slug,
                    description=description.strip() or None,
                )
                session.add(workspace)
                await session.flush()

                if created_by:
                    member = WorkspaceMember(
                        user_id=uuid.UUID(created_by),
                        workspace_id=workspace.id,
                        role=UserRole.ADMIN,
                        is_primary=False,
                        is_active=True,
                    )
                    session.add(member)

        logger.info(f"Workspace created: {slug}")
        return workspace

    async def add_member(
        self,
        workspace_id: str,
        user_id: str,
        role: UserRole = UserRole.VIEWER,
    ) -> WorkspaceMember:
        """Add a user to a workspace. Safe against duplicates."""
        ws_uuid = uuid.UUID(workspace_id)
        u_uuid = uuid.UUID(user_id)

        async with self._session_factory() as session:
            async with session.begin():
                ws = await session.scalar(select(Workspace).where(Workspace.id == ws_uuid))
                if not ws:
                    raise ValueError(f"Workspace not found: {workspace_id}")
                if not ws.is_active:
                    raise ValueError("Cannot add member to inactive workspace")

                # Check for existing membership
                existing = await session.scalar(
                    select(WorkspaceMember).where(
                        WorkspaceMember.user_id == u_uuid,
                        WorkspaceMember.workspace_id == ws_uuid,
                    )
                )
                if existing:
                    raise ValueError("User is already a member of this workspace")

                user_exists = await session.scalar(select(User.id).where(User.id == u_uuid))
                if not user_exists:
                    raise ValueError(f"User not found: {user_id}")

                member = WorkspaceMember(
                    user_id=u_uuid,
                    workspace_id=ws_uuid,
                    role=role,
                    is_active=True,
                )
                session.add(member)

        logger.info(f"Member added: user={user_id[:8]}... to workspace={workspace_id}")
        return member

    async def get_or_create_default_workspace(self) -> Workspace:
        """Ensure a 'default' workspace exists. Handles race conditions."""
        # Quick read check first
        async with self._session_factory() as session:
            ws = await session.scalar(select(Workspace).where(Workspace.slug == "default"))
            if ws:
                return ws

        # Creation attempt with retry
        for attempt in range(2):
            async with self._session_factory() as session:
                try:
                    async with session.begin():
                        ws = await session.scalar(select(Workspace).where(Workspace.slug == "default"))
                        if ws:
                            return ws

                        ws = Workspace(
                            name="Default Workspace",
                            slug="default",
                            description="Auto-created default workspace",
                            is_active=True,
                        )
                        session.add(ws)
                    logger.info("Default workspace created.")
                    return ws
                except IntegrityError:
                    await session.rollback()
                    if attempt == 0:
                        logger.debug("Race condition on default workspace creation, retrying...")
                        continue
                    # Fallback: fetch existing after conflict
                    async with self._session_factory() as fallback_session:
                        ws = await fallback_session.scalar(select(Workspace).where(Workspace.slug == "default"))
                        if ws:
                            return ws
                    raise RuntimeError("Failed to resolve default workspace after race condition")

        raise RuntimeError("Failed to create/get default workspace")

    async def get_workspace_by_slug(self, slug: str) -> Optional[Workspace]:
        """Safe lookup by slug."""
        async with self._session_factory() as session:
            return await session.scalar(select(Workspace).where(Workspace.slug == validate_slug(slug)))

    async def get_workspace_stats(self, workspace_id: str) -> Dict[str, Any]:
        """Usage metrics for a workspace."""
        ws_uuid = uuid.UUID(workspace_id)

        try:
            async with self._session_factory() as session:
                user_count = await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceMember)
                    .where(
                        WorkspaceMember.workspace_id == ws_uuid,
                        WorkspaceMember.is_active == True,
                    )
                )

                # NOTE: This endpoint returns membership stats only. Document and
                # query counts are served by the dedicated monitoring endpoint
                # (/api/v1/monitoring/stats) which owns the ingestion + provenance
                # tables — kept separate to avoid cross-module coupling here.
                doc_count = 0
                query_count = 0

                return {
                    "workspace_id": workspace_id,
                    "doc_count": doc_count,
                    "user_count": user_count or 0,
                    "query_count": query_count,
                }
        except Exception as e:
            logger.warning(f"Failed to fetch workspace stats for {workspace_id}: {e}")
            return {
                "workspace_id": workspace_id,
                "doc_count": 0,
                "user_count": 0,
                "query_count": 0,
            }


# ========================================================================
# -- LOCAL TESTING ENTRY POINT (Run: python -m app.auth.store) -----------
# ========================================================================

