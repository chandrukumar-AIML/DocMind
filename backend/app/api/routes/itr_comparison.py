"""ITR Year-on-Year Comparison — Feature #11.

Extracts key financial fields from two ITR documents (current + previous year)
and returns a structured comparison: income, deductions, tax paid, refund due, etc.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.comparison_engine import _fetch_doc_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/itr", tags=["itr"])

# ── Schemas ───────────────────────────────────────────────────────────────────

class ItrCompareRequest(BaseModel):
    doc_current: str        # source_file of current-year ITR
    doc_previous: str       # source_file of previous-year ITR
    workspace_id: Optional[str] = None


class ItrField(BaseModel):
    field:      str
    current:    str
    previous:   str
    change:     str         # "↑ 12%", "↓ 5%", "Same", "New", "Removed"
    note:       Optional[str] = None


class ItrCompareResponse(BaseModel):
    summary:    str
    fields:     list[ItrField]
    red_flags:  list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _fetch(doc_id: str, workspace_id: str) -> str:
    chunks = await asyncio.to_thread(_fetch_doc_chunks, doc_id, workspace_id, k=20)
    return "\n\n".join(c.page_content for c in chunks) if chunks else ""


_SYSTEM = """You are a CA assistant specialized in Indian Income Tax Return analysis.
You are given text from TWO ITR documents — the CURRENT year and the PREVIOUS year.
Extract and compare key financial figures.

Return ONLY valid JSON (no markdown fences) in this exact shape:
{
  "summary": "One-sentence overall comparison",
  "fields": [
    {"field": "Gross Total Income", "current": "₹X", "previous": "₹Y", "change": "↑ 12%", "note": "optional"},
    ...
  ],
  "red_flags": ["Optional list of concerns like large unexplained drops, new deductions, etc."]
}

Fields to compare (include all that are present):
Gross Total Income, Total Deductions (80C/80D/etc.), Taxable Income, Tax Payable,
TDS Deducted, Advance Tax Paid, Self-Assessment Tax, Refund Due / Tax Payable Net,
Income from Salary, Income from Business/Profession, Income from Capital Gains,
Income from Other Sources, HRA Exemption, Standard Deduction, Section 80C, Section 80D.

For change: calculate % change if both values are numeric; otherwise describe qualitatively.
Mark "New" if field appears only in current, "Removed" if only in previous.
"""


def _parse(text: str) -> dict:
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```[a-z]*\n?", "", clean)
        clean = re.sub(r"\n?```$", "", clean)
    m = re.search(r"\{.*\}", clean, re.DOTALL)
    if not m:
        raise ValueError("LLM returned no JSON object")
    return json.loads(m.group())


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/compare", response_model=ItrCompareResponse)
async def compare_itr(
    req: ItrCompareRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    workspace_id = req.workspace_id or user.workspace_id

    text_curr, text_prev = await asyncio.gather(
        _fetch(req.doc_current,  workspace_id),
        _fetch(req.doc_previous, workspace_id),
    )

    if not text_curr:
        raise HTTPException(400, f"No content found for current-year doc: {req.doc_current}")
    if not text_prev:
        raise HTTPException(400, f"No content found for previous-year doc: {req.doc_previous}")

    prompt = (
        f"CURRENT YEAR ITR:\n{text_curr[:4000]}\n\n"
        f"---\n\nPREVIOUS YEAR ITR:\n{text_prev[:4000]}"
    )

    try:
        from app.core.llm import get_vision_llm
        llm = get_vision_llm()
        from langchain_core.messages import HumanMessage, SystemMessage
        resp = await asyncio.to_thread(
            llm.invoke,
            [SystemMessage(content=_SYSTEM), HumanMessage(content=prompt)],
        )
        raw = resp.content if hasattr(resp, "content") else str(resp)
        data = _parse(raw)
    except Exception as e:
        logger.warning(f"LLM ITR comparison failed: {e}")
        data = {
            "summary": "Could not extract structured comparison from the provided documents.",
            "fields": [],
            "red_flags": [f"LLM error: {e}"],
        }

    fields = [ItrField(**f) for f in data.get("fields", [])]
    return ItrCompareResponse(
        summary=data.get("summary", ""),
        fields=fields,
        red_flags=data.get("red_flags", []),
    )
