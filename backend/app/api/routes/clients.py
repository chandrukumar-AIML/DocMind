from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.database.session import get_async_db as get_async_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/clients", tags=["clients"])


# ── Pydantic models ──────────────────────────────────────────────────────


class ClientCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    gstin: Optional[str] = Field(default=None, max_length=15)
    pan: Optional[str] = Field(default=None, max_length=10)
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=1000)
    workspace_id: Optional[str] = None


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    gstin: Optional[str] = Field(default=None, max_length=15)
    pan: Optional[str] = Field(default=None, max_length=10)
    phone: Optional[str] = Field(default=None, max_length=20)
    email: Optional[str] = Field(default=None, max_length=255)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ClientOut(BaseModel):
    id: str
    workspace_id: str
    name: str
    gstin: Optional[str]
    pan: Optional[str]
    phone: Optional[str]
    email: Optional[str]
    notes: Optional[str]
    created_at: str
    doc_count: int = 0

    model_config = {"from_attributes": True}


class AssignDocRequest(BaseModel):
    document_id: str = Field(..., max_length=512)
    client_id: Optional[str] = None  # None = unassign
    workspace_id: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _resolve_workspace(user: AuthenticatedUser, workspace_id: Optional[str]) -> str:
    wid = workspace_id or user.workspace_id
    if not wid:
        raise HTTPException(status_code=400, detail="workspace_id required")
    return wid


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("", response_model=list[ClientOut])
async def list_clients(
    workspace_id: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    from app.auth.models import Client, DocumentClient

    wid = _resolve_workspace(current_user, workspace_id)
    result = await db.execute(
        select(Client).where(Client.workspace_id == uuid.UUID(wid), Client.is_active == True)
        .order_by(Client.name)
    )
    clients = result.scalars().all()

    # Count docs per client
    doc_counts: dict[str, int] = {}
    if clients:
        ids = [c.id for c in clients]
        dc_result = await db.execute(
            select(DocumentClient.client_id, DocumentClient.id)
            .where(DocumentClient.client_id.in_(ids))
        )
        for row in dc_result.all():
            cid = str(row[0])
            doc_counts[cid] = doc_counts.get(cid, 0) + 1

    out = []
    for c in clients:
        out.append(ClientOut(
            id=str(c.id),
            workspace_id=str(c.workspace_id),
            name=c.name,
            gstin=c.gstin,
            pan=c.pan,
            phone=c.phone,
            email=c.email,
            notes=c.notes,
            created_at=c.created_at.isoformat(),
            doc_count=doc_counts.get(str(c.id), 0),
        ))
    return out


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    from app.auth.models import Client

    wid = _resolve_workspace(current_user, body.workspace_id)
    client = Client(
        workspace_id=uuid.UUID(wid),
        name=body.name.strip(),
        gstin=body.gstin,
        pan=body.pan,
        phone=body.phone,
        email=body.email,
        notes=body.notes,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return ClientOut(
        id=str(client.id),
        workspace_id=str(client.workspace_id),
        name=client.name,
        gstin=client.gstin,
        pan=client.pan,
        phone=client.phone,
        email=client.email,
        notes=client.notes,
        created_at=client.created_at.isoformat(),
        doc_count=0,
    )


@router.patch("/{client_id}", response_model=ClientOut)
async def update_client(
    client_id: str,
    body: ClientUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    from app.auth.models import Client

    result = await db.execute(select(Client).where(Client.id == uuid.UUID(client_id)))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    if body.name is not None:
        client.name = body.name.strip()
    if body.gstin is not None:
        client.gstin = body.gstin
    if body.pan is not None:
        client.pan = body.pan
    if body.phone is not None:
        client.phone = body.phone
    if body.email is not None:
        client.email = body.email
    if body.notes is not None:
        client.notes = body.notes

    await db.commit()
    await db.refresh(client)
    return ClientOut(
        id=str(client.id),
        workspace_id=str(client.workspace_id),
        name=client.name,
        gstin=client.gstin,
        pan=client.pan,
        phone=client.phone,
        email=client.email,
        notes=client.notes,
        created_at=client.created_at.isoformat(),
    )


@router.delete("/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_client(
    client_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    from app.auth.models import Client

    result = await db.execute(select(Client).where(Client.id == uuid.UUID(client_id)))
    client = result.scalar_one_or_none()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    client.is_active = False
    await db.commit()


@router.post("/assign-document", status_code=status.HTTP_200_OK)
async def assign_document(
    body: AssignDocRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    from app.auth.models import DocumentClient

    wid = _resolve_workspace(current_user, body.workspace_id)

    # Remove existing assignment
    await db.execute(
        delete(DocumentClient).where(
            DocumentClient.workspace_id == uuid.UUID(wid),
            DocumentClient.document_id == body.document_id,
        )
    )

    if body.client_id:
        dc = DocumentClient(
            workspace_id=uuid.UUID(wid),
            document_id=body.document_id,
            client_id=uuid.UUID(body.client_id),
        )
        db.add(dc)

    await db.commit()
    return {"status": "ok", "document_id": body.document_id, "client_id": body.client_id}


@router.get("/document-map", response_model=dict)
async def get_document_client_map(
    workspace_id: Optional[str] = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
):
    """Returns {document_id: client_id} map for the workspace."""
    from app.auth.models import DocumentClient

    wid = _resolve_workspace(current_user, workspace_id)
    result = await db.execute(
        select(DocumentClient.document_id, DocumentClient.client_id)
        .where(DocumentClient.workspace_id == uuid.UUID(wid))
    )
    return {row[0]: str(row[1]) for row in result.all()}
