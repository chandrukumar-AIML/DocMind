"""
Document-level ACL enforcement utilities.

Usage (in retrieval routes / RAG chain):

    from app.auth.document_acl import filter_documents_by_acl, can_access_document

    # Filter a list of (Document, score) results to only those the user can access
    allowed = await filter_documents_by_acl(
        db, user_id=str(user.user_id), workspace_id=workspace_id, docs=reranked
    )

Design:
- If NO DocumentPermission rows exist for a document in this workspace, access
  follows the workspace-level role (all workspace members can access it).
- If ANY rows exist, access is restricted to listed users PLUS workspace admins/superusers.
- Workspace admins (role="workspace_admin" or "admin") and superusers always bypass ACL.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import DocumentPermission, WorkspaceMember

logger = logging.getLogger(__name__)


async def can_access_document(
    db: AsyncSession,
    user_id: str,
    workspace_id: str,
    document_id: str,
    is_admin: bool = False,
) -> bool:
    """
    Return True if `user_id` may access `document_id` in `workspace_id`.

    Admins and superusers always return True.
    Documents without any ACL rows are open to all workspace members.
    Documents with ACL rows require an explicit grant.
    """
    if is_admin:
        return True

    # Check if any ACL rows exist for this document in this workspace
    acl_count = await db.scalar(
        select(func.count(DocumentPermission.id)).where(
            DocumentPermission.document_id == document_id,
            DocumentPermission.workspace_id == workspace_id,
        )
    )

    if not acl_count:
        # No ACL rows → workspace-default (all members can access)
        return True

    # ACL rows exist → check if this user is listed
    grant = await db.scalar(
        select(DocumentPermission.id).where(
            DocumentPermission.document_id == document_id,
            DocumentPermission.workspace_id == workspace_id,
            DocumentPermission.user_id == user_id,
        )
    )
    return grant is not None


async def filter_documents_by_acl(
    db: AsyncSession,
    user_id: str,
    workspace_id: str,
    docs: List[Tuple[Any, float]],
    is_admin: bool = False,
) -> List[Tuple[Any, float]]:
    """
    Filter a list of (Document, score) tuples, removing documents the user
    cannot access per the document-level ACL.

    Documents without a ``source_file`` metadata key are passed through (no ACL
    can be checked without an identifier).
    """
    if is_admin:
        return docs

    allowed = []
    for doc, score in docs:
        source_file: Optional[str] = getattr(doc, "metadata", {}).get("source_file")
        if not source_file or source_file.startswith("web:"):
            # Web results and unidentified chunks are not subject to document ACL
            allowed.append((doc, score))
            continue

        try:
            ok = await can_access_document(
                db=db,
                user_id=user_id,
                workspace_id=workspace_id,
                document_id=source_file,
                is_admin=is_admin,
            )
        except Exception as e:
            logger.warning(f"ACL check failed for {source_file}: {e} — defaulting to allow")
            ok = True  # fail-open on DB error to avoid data loss

        if ok:
            allowed.append((doc, score))
        else:
            logger.debug(f"ACL: user {user_id} denied access to {source_file}")

    return allowed


async def grant_document_permission(
    db: AsyncSession,
    document_id: str,
    workspace_id: str,
    user_id: str,
    granted_by: str,
    permission: str = "view",
) -> DocumentPermission:
    """Grant a user permission to access a specific document."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(DocumentPermission)
        .values(
            document_id=document_id,
            workspace_id=workspace_id,
            user_id=user_id,
            granted_by=granted_by,
            permission=permission,
        )
        .on_conflict_do_update(
            constraint="uq_doc_perm_user",
            set_={"permission": permission, "granted_by": granted_by},
        )
        .returning(DocumentPermission)
    )
    result = await db.execute(stmt)
    await db.commit()
    return result.scalar_one()


async def revoke_document_permission(
    db: AsyncSession,
    document_id: str,
    workspace_id: str,
    user_id: str,
) -> bool:
    """Revoke a user's explicit permission for a document. Returns True if a row was deleted."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(DocumentPermission).where(
            DocumentPermission.document_id == document_id,
            DocumentPermission.workspace_id == workspace_id,
            DocumentPermission.user_id == user_id,
        )
    )
    await db.commit()
    return result.rowcount > 0
