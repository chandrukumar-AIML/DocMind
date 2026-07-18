"""WhatsApp Chat Ingestion — Feature #8.

Accepts a WhatsApp exported .txt file, strips timestamps and phone numbers,
groups messages by sender, and ingests the cleaned text into the vector store
so CAs can query WhatsApp conversations with clients.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, AuthenticatedUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

_MAX_SIZE = 5 * 1024 * 1024   # 5 MB

# ── WhatsApp format patterns ──────────────────────────────────────────────────
# Handles both Android and iOS export formats:
#   Android: "DD/MM/YYYY, HH:MM - Sender: message"
#   iOS:     "[DD/MM/YYYY, HH:MM:SS] Sender: message"

_WA_LINE_RE = re.compile(
    r"^(?:\[)?(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?)(?:\])?[\s\-–]+(.+?):\s(.+)$",
    re.MULTILINE,
)

_OMIT_SENDERS = {"Messages and calls are end-to-end encrypted"}
_SYSTEM_MSG_RE = re.compile(r"^.+? (added|removed|left|joined|changed|created|deleted).+$", re.IGNORECASE)


def _clean_whatsapp(raw: str) -> tuple[str, dict]:
    """
    Parse WA export → clean transcript string + metadata dict.
    Returns (cleaned_text, {senders, message_count, date_range}).
    """
    messages = []
    senders = set()
    dates = []

    for m in _WA_LINE_RE.finditer(raw):
        date_str, time_str, sender, body = m.group(1), m.group(2), m.group(3), m.group(4)
        if sender in _OMIT_SENDERS:
            continue
        if _SYSTEM_MSG_RE.match(body):
            continue
        if body.strip() in ("<Media omitted>", "<image omitted>", "<video omitted>", "<audio omitted>", "<document omitted>"):
            body = "[attachment]"
        senders.add(sender)
        dates.append(date_str)
        messages.append(f"{sender}: {body.strip()}")

    cleaned = "\n".join(messages)
    meta = {
        "senders": sorted(senders),
        "message_count": len(messages),
        "date_range": f"{dates[0]} – {dates[-1]}" if dates else "unknown",
    }
    return cleaned, meta


# ── Response ──────────────────────────────────────────────────────────────────

class WhatsAppIngestResponse(BaseModel):
    source_file:   str
    message_count: int
    senders:       list[str]
    date_range:    str
    chunk_count:   int


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/ingest", response_model=WhatsAppIngestResponse)
async def ingest_whatsapp(
    file:         UploadFile = File(...),
    workspace_id: Optional[str] = Form(default=None),
    label:        Optional[str] = Form(default=None),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Upload a WhatsApp .txt export and ingest it into the document store.
    The conversation is cleaned, chunked, and embedded like any other document.
    """
    if not file.filename.endswith(".txt"):
        raise HTTPException(status_code=422, detail="Only .txt WhatsApp exports are supported.")

    raw_bytes = await file.read()
    if len(raw_bytes) > _MAX_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB).")

    # Try utf-8, fall back to latin-1 (common in WA exports from older phones)
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = raw_bytes.decode("latin-1")

    cleaned, meta = _clean_whatsapp(raw)

    if meta["message_count"] == 0:
        raise HTTPException(
            status_code=422,
            detail="No WhatsApp messages found. Make sure you exported the chat as a .txt file from WhatsApp.",
        )

    wsid = workspace_id or user.workspace_id
    chat_name = label or file.filename.replace(".txt", "")
    source_file = f"whatsapp/{wsid}/{chat_name}.txt"

    # Ingest via the vector store manager
    chunk_count = 0
    try:
        from app.dependencies import get_store_manager
        from langchain_core.documents import Document

        vsm = await asyncio.to_thread(get_store_manager)

        chunk_size = 700
        raw_chunks = [cleaned[i:i+chunk_size] for i in range(0, len(cleaned), chunk_size)]
        base_meta = {
            "source": source_file,
            "workspace_id": wsid,
            "type": "whatsapp",
            "senders": ", ".join(meta["senders"]),
            "message_count": str(meta["message_count"]),
            "date_range": meta["date_range"],
        }
        child_docs = [
            Document(page_content=c, metadata={**base_meta, "chunk_id": f"{source_file}_{i}", "parent_id": source_file})
            for i, c in enumerate(raw_chunks)
        ]
        parent_doc = Document(page_content=cleaned[:2000], metadata={**base_meta, "chunk_id": source_file})
        await asyncio.to_thread(vsm.ingest_chunks, child_docs, [parent_doc])
        chunk_count = len(child_docs)
    except Exception as e:
        logger.error(f"WhatsApp ingest vector store failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)[:200]}")

    return WhatsAppIngestResponse(
        source_file=source_file,
        message_count=meta["message_count"],
        senders=meta["senders"],
        date_range=meta["date_range"],
        chunk_count=chunk_count,
    )
