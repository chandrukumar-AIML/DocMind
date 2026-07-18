"""Draft Reply Generation — CA-grade reply letters for GST/income-tax notices."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/domains", tags=["draft-reply"])

_LLM_TIMEOUT = 120.0
_CHUNK_CHARS  = 3000

# ── Prompts ──────────────────────────────────────────────────────────────────

_REPLY_PROMPT = """You are a senior Chartered Accountant drafting a professional statutory reply letter on behalf of a taxpayer/client in India.

## Notice / Show Cause Notice (extracted text)
{notice_text}

## Supporting Documents (client's filed returns, invoices, etc.)
{support_text}

## Additional Context from CA
{context}

---
Draft a complete, professional reply letter to this notice. The letter must:

1. Start with proper letter format:
   - Date: [Date]
   - To: The jurisdictional officer named in the notice (extract from notice text, or use "The Jurisdictional GST Officer")
   - Subject: Reply to [Notice Type] dated [Notice Date] — Reference No. [Reference Number]

2. Opening paragraph: Acknowledge receipt of the notice, cite the reference number and date.

3. Point-by-point response: For each objection/demand in the notice:
   - Quote the department's allegation
   - Provide the factual rebuttal, citing specific amounts, dates, and sections from the supporting documents
   - Cite the relevant section of GST Act / Income Tax Act / other statute

4. Relief sought: Clearly state what relief the taxpayer is requesting (drop of demand, acceptance of ITC, etc.)

5. Enclosures list: List the documents being attached as evidence.

6. Closing: "We trust the above clarification and documents are self-explanatory. We request you to kindly drop the demand and close the proceedings."

7. Sign-off: "Yours faithfully, [Name of CA / Authorised Signatory], [Designation], For [Firm/Company Name]"

Use formal Indian legal letter language. Be specific — cite actual figures from the documents. Do NOT hallucinate amounts or dates not present in the provided text.

Format the output as clean Markdown (use ## for sections, **bold** for amounts/dates, tables where useful for comparing figures).
"""

_NOTICE_ONLY_PROMPT = """You are a senior Chartered Accountant drafting a professional statutory reply letter in India.

## Notice Text
{notice_text}

## Additional Context from CA
{context}

Draft a complete reply letter to this notice. Since supporting documents are not yet available, draft the reply with [PLACEHOLDER] markers where the CA should insert specific figures, dates, and document references.

Structure:
1. Proper letter heading (To, Subject, Date)
2. Acknowledgement paragraph
3. Point-by-point replies to each allegation with [PLACEHOLDER: insert actual figure/date] markers
4. Relief sought
5. Enclosures list with [PLACEHOLDER: list actual documents]
6. Formal sign-off

Use formal Indian legal language. Mark all placeholders clearly so the CA knows what to fill in.
"""


# ── Request / Response ────────────────────────────────────────────────────────

class DraftReplyRequest(BaseModel):
    notice_file: str       = Field(..., max_length=512)
    supporting_files: list[str] = Field(default_factory=list, max_length=10)
    reply_context: str     = Field(default="", max_length=2000)
    workspace_id: Optional[str] = None


class DraftReplyResponse(BaseModel):
    draft: str
    notice_file: str
    supporting_files: list[str]
    correlation_id: str


# ── Helper ────────────────────────────────────────────────────────────────────

async def _fetch_chunks(source_file: str, workspace_id: str, limit_chars: int = _CHUNK_CHARS) -> str:
    try:
        from app.core.comparison_engine import _fetch_doc_chunks
        text = await _fetch_doc_chunks(source_file, workspace_id)
        return text[:limit_chars]
    except Exception as e:
        logger.warning(f"Could not fetch chunks for {source_file}: {e}")
        return f"[Could not retrieve text from {source_file}]"


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/draft-reply", response_model=DraftReplyResponse)
async def draft_reply(
    req: DraftReplyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> DraftReplyResponse:
    """Generate a CA-grade reply letter grounded in the uploaded notice and supporting docs."""
    corr_id = generate_correlation_id("draft")
    workspace_id = req.workspace_id or user.workspace_id

    # Fetch notice text
    notice_text = await _fetch_chunks(req.notice_file, workspace_id, limit_chars=4000)

    # Fetch supporting docs (up to 3, 2000 chars each)
    support_parts: list[str] = []
    for sf in req.supporting_files[:3]:
        text = await _fetch_chunks(sf, workspace_id, limit_chars=2000)
        short = sf.split("/")[-1].split("\\")[-1]
        support_parts.append(f"### {short}\n{text}")

    support_text = "\n\n".join(support_parts) if support_parts else "No supporting documents provided."
    context = req.reply_context.strip() or "No additional context provided."

    prompt = (
        _REPLY_PROMPT if support_parts else _NOTICE_ONLY_PROMPT
    ).format(
        notice_text=notice_text,
        support_text=support_text,
        context=context,
    )

    try:
        from app.core.vision_llm import get_vision_llm
        llm = get_vision_llm()
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: llm.invoke(prompt)),
            timeout=_LLM_TIMEOUT,
        )
        draft = raw.content if hasattr(raw, "content") else str(raw)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Draft generation timed out — try with fewer supporting documents.")
    except Exception as e:
        logger.error(f"[{corr_id}] Draft reply LLM failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Draft generation failed")

    return DraftReplyResponse(
        draft=draft,
        notice_file=req.notice_file,
        supporting_files=req.supporting_files,
        correlation_id=corr_id,
    )
