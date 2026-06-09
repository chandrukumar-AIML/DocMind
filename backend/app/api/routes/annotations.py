# backend/app/api/routes/annotations.py
"""Collaborative annotation API with WebSocket real-time broadcast."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.annotation_store import (
    create_annotation,
    get_annotations,
    resolve_annotation,
    delete_annotation,
    _VALID_TYPES,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/annotations", tags=["annotations"])

# ── WebSocket connection manager ──────────────────────────────


class _ConnectionManager:
    def __init__(self):
        # workspace_id → source_file → list[WebSocket]
        self._rooms: dict[str, dict[str, list[WebSocket]]] = {}

    def _room(self, workspace_id: str, source_file: str) -> list[WebSocket]:
        return self._rooms.setdefault(workspace_id, {}).setdefault(source_file, [])

    async def connect(self, ws: WebSocket, workspace_id: str, source_file: str):
        await ws.accept()
        self._room(workspace_id, source_file).append(ws)

    def disconnect(self, ws: WebSocket, workspace_id: str, source_file: str):
        room = self._room(workspace_id, source_file)
        if ws in room:
            room.remove(ws)

    async def broadcast(self, workspace_id: str, source_file: str, message: dict):
        payload = json.dumps(message, default=str)
        dead = []
        for ws in list(self._room(workspace_id, source_file)):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws, workspace_id, source_file)


_manager = _ConnectionManager()


# ── Pydantic models ────────────────────────────────────────────


class AnnotationCreateRequest(BaseModel):
    source_file: str = Field(..., min_length=1, max_length=1024)
    type: str = Field(...)
    content: Optional[str] = Field(default=None, max_length=5000)
    page_number: Optional[int] = Field(default=None, ge=1)
    position: Optional[dict] = None
    parent_id: Optional[str] = None


# ── REST endpoints ────────────────────────────────────────────


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def add_annotation(
    req: AnnotationCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("ann-create")
    if req.type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail=f"Invalid type. Valid: {_VALID_TYPES}")

    try:
        ann = await create_annotation(
            workspace_id=user.workspace_id,
            source_file=req.source_file,
            user_id=user.user_id,
            username=getattr(user, "username", user.user_id),
            annotation_type=req.type,
            content=req.content,
            page_number=req.page_number,
            position=req.position,
            parent_id=req.parent_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to create annotation: {e}")
        raise HTTPException(status_code=500, detail="Failed to create annotation")

    # Broadcast to all viewers of this document
    asyncio.create_task(
        _manager.broadcast(
            user.workspace_id,
            req.source_file,
            {
                "event": "annotation_created",
                "annotation": ann,
            },
        )
    )

    ann["correlation_id"] = corr_id
    return ann


@router.get("/list")
async def list_annotations(
    source_file: str,
    type: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("ann-list")
    try:
        annotations = await get_annotations(user.workspace_id, source_file, type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch annotations: {e}")

    return {
        "source_file": source_file,
        "annotations": annotations,
        "total": len(annotations),
        "correlation_id": corr_id,
    }


@router.post("/{annotation_id}/resolve")
async def resolve(
    annotation_id: str,
    source_file: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("ann-resolve")
    ok = await resolve_annotation(annotation_id, user.workspace_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Annotation not found")

    asyncio.create_task(
        _manager.broadcast(
            user.workspace_id,
            source_file,
            {
                "event": "annotation_resolved",
                "annotation_id": annotation_id,
                "resolved_by": user.user_id,
            },
        )
    )
    return {"resolved": True, "annotation_id": annotation_id, "correlation_id": corr_id}


@router.delete("/{annotation_id}")
async def remove_annotation(
    annotation_id: str,
    source_file: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("ann-del")
    ok = await delete_annotation(annotation_id, user.workspace_id, user.user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Annotation not found or not yours")

    asyncio.create_task(
        _manager.broadcast(
            user.workspace_id,
            source_file,
            {
                "event": "annotation_deleted",
                "annotation_id": annotation_id,
            },
        )
    )
    return {"deleted": True, "annotation_id": annotation_id, "correlation_id": corr_id}


# ── WebSocket endpoint ────────────────────────────────────────


@router.websocket("/ws/{workspace_id}")
async def annotation_ws(
    websocket: WebSocket,
    workspace_id: str,
    source_file: str,
):
    """WebSocket for real-time annotation sync. Connect with ?source_file=<path>"""
    await _manager.connect(websocket, workspace_id, source_file)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # Echo back to other clients in same room
                await _manager.broadcast(
                    workspace_id,
                    source_file,
                    {
                        "event": "annotation_update",
                        "data": msg,
                    },
                )
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        _manager.disconnect(websocket, workspace_id, source_file)


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Annotation routes smoke test")
        mgr = _ConnectionManager()
        assert mgr._rooms == {}
        req = AnnotationCreateRequest(
            source_file="test.pdf",
            type="comment",
            content="This clause looks risky",
            page_number=3,
        )
        assert req.type == "comment"
        print("AnnotationCreateRequest validation OK")
        print("Annotation routes checks passed")

    asyncio.run(smoke())
