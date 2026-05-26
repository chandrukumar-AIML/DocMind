# backend/app/core/annotation_store.py
"""Collaborative annotation store with PostgreSQL persistence."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_VALID_TYPES = {"highlight", "comment", "tag", "risk_flag", "approval"}


async def ensure_annotation_schema() -> None:
    """Create annotations table."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS annotations (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id  VARCHAR(64) NOT NULL,
                source_file   TEXT NOT NULL,
                user_id       VARCHAR(64) NOT NULL,
                username      VARCHAR(128),
                type          VARCHAR(32) NOT NULL,
                content       TEXT,
                page_number   INTEGER,
                position      JSONB,
                resolved      BOOLEAN NOT NULL DEFAULT FALSE,
                parent_id     UUID,
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """))
        for idx in [
            "CREATE INDEX IF NOT EXISTS ix_annotations_workspace ON annotations(workspace_id)",
            "CREATE INDEX IF NOT EXISTS ix_annotations_source_file ON annotations(source_file)",
            "CREATE INDEX IF NOT EXISTS ix_annotations_user ON annotations(user_id)",
        ]:
            await conn.execute(text(idx))
    logger.info("Annotation schema verified")


async def create_annotation(
    workspace_id: str,
    source_file: str,
    user_id: str,
    username: str,
    annotation_type: str,
    content: Optional[str],
    page_number: Optional[int],
    position: Optional[dict],
    parent_id: Optional[str] = None,
) -> dict[str, Any]:
    if annotation_type not in _VALID_TYPES:
        raise ValueError(f"Invalid annotation type: {annotation_type}")

    ann_id = str(uuid.uuid4())
    async with async_engine.begin() as conn:
        await conn.execute(text("""
            INSERT INTO annotations
                (id, workspace_id, source_file, user_id, username, type,
                 content, page_number, position, parent_id)
            VALUES
                (:id, :ws, :sf, :uid, :uname, :type,
                 :content, :page, CAST(:pos AS jsonb), :parent)
        """), {
            "id": ann_id,
            "ws": workspace_id,
            "sf": source_file,
            "uid": user_id,
            "uname": username,
            "type": annotation_type,
            "content": content,
            "page": page_number,
            "pos": json.dumps(position) if position else "{}",
            "parent": parent_id,
        })

    return {
        "id": ann_id,
        "workspace_id": workspace_id,
        "source_file": source_file,
        "user_id": user_id,
        "username": username,
        "type": annotation_type,
        "content": content,
        "page_number": page_number,
        "position": position,
        "parent_id": parent_id,
        "resolved": False,
    }


async def get_annotations(
    workspace_id: str,
    source_file: str,
    annotation_type: Optional[str] = None,
) -> list[dict]:
    query = """
        SELECT id, user_id, username, type, content, page_number, position,
               resolved, parent_id, created_at, updated_at
        FROM annotations
        WHERE workspace_id = :ws AND source_file = :sf
    """
    params: dict[str, Any] = {"ws": workspace_id, "sf": source_file}
    if annotation_type:
        query += " AND type = :type"
        params["type"] = annotation_type
    query += " ORDER BY created_at ASC"

    async with async_engine.begin() as conn:
        rows = await conn.execute(text(query), params)
        results = rows.fetchall()

    return [
        {
            "id": str(r[0]),
            "user_id": r[1],
            "username": r[2],
            "type": r[3],
            "content": r[4],
            "page_number": r[5],
            "position": r[6] if isinstance(r[6], dict) else {},
            "resolved": r[7],
            "parent_id": str(r[8]) if r[8] else None,
            "created_at": r[9].isoformat() if r[9] else None,
            "updated_at": r[10].isoformat() if r[10] else None,
        }
        for r in results
    ]


async def resolve_annotation(
    annotation_id: str,
    workspace_id: str,
    user_id: str,
) -> bool:
    async with async_engine.begin() as conn:
        result = await conn.execute(text("""
            UPDATE annotations
            SET resolved = TRUE, updated_at = NOW()
            WHERE id = :id AND workspace_id = :ws
        """), {"id": annotation_id, "ws": workspace_id})
    return result.rowcount > 0


async def delete_annotation(
    annotation_id: str,
    workspace_id: str,
    user_id: str,
) -> bool:
    async with async_engine.begin() as conn:
        result = await conn.execute(text("""
            DELETE FROM annotations
            WHERE id = :id AND workspace_id = :ws AND user_id = :uid
        """), {"id": annotation_id, "ws": workspace_id, "uid": user_id})
    return result.rowcount > 0


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Annotation store smoke test")
        assert "highlight" in _VALID_TYPES
        assert "comment" in _VALID_TYPES
        assert "risk_flag" in _VALID_TYPES
        try:
            await create_annotation("ws", "test.pdf", "u1", "Alice",
                                    "invalid_type", "note", None, None)
            assert False, "Should have raised"
        except ValueError:
            print("Invalid type rejection OK")
        print("Annotation store checks passed")

    asyncio.run(smoke())
