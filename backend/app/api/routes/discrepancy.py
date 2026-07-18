"""Discrepancy Auto-Detection — Feature #5.

Scans two documents and returns a structured list of numeric / factual
mismatches without requiring the user to ask a question.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/discrepancy", tags=["discrepancy"])

_LLM_TIMEOUT = 90.0
_CHUNK_CHARS  = 3500

# ── Prompt ────────────────────────────────────────────────────────────────────

_DISCREPANCY_PROMPT = """You are a Chartered Accountant's document review assistant specialising in Indian tax compliance.

## Document A — {name_a}
{text_a}

## Document B — {name_b}
{text_b}

---
Your task: identify ALL discrepancies, mismatches, and inconsistencies between these two documents.

Focus on:
- Numeric differences (amounts, tax figures, quantities, percentages)
- Date mismatches
- GSTIN / PAN / reference number conflicts
- Category or head of income differences
- ITC claimed vs ITC available differences
- Taxable value vs reported value gaps

Output a JSON array ONLY — no preamble, no explanation outside the JSON.
Each object must have exactly these fields:
{{
  "field": "<what is being compared, e.g. 'Taxable Value', 'IGST Paid'>",
  "doc_a_value": "<value from Document A, or 'Not found'>",
  "doc_b_value": "<value from Document B, or 'Not found'>",
  "severity": "high" | "medium" | "low",
  "note": "<one-sentence CA-grade explanation of why this matters>"
}}

Severity guide:
- high   = could result in demand, penalty, or prosecution
- medium = needs clarification before filing / replying
- low    = minor inconsistency, may be rounding or format difference

If no discrepancies are found, return an empty array: []
"""

# ── Schemas ───────────────────────────────────────────────────────────────────

class DiscrepancyRequest(BaseModel):
    doc_a: str = Field(..., max_length=512)
    doc_b: str = Field(..., max_length=512)
    workspace_id: Optional[str] = None


class DiscrepancyItem(BaseModel):
    field:       str
    doc_a_value: str
    doc_b_value: str
    severity:    str   # "high" | "medium" | "low"
    note:        str


class DiscrepancyResponse(BaseModel):
    discrepancies:  list[DiscrepancyItem]
    doc_a:          str
    doc_b:          str
    total:          int
    high_count:     int
    correlation_id: str


# ── Helper ────────────────────────────────────────────────────────────────────

async def _fetch(source_file: str, workspace_id: str) -> str:
    try:
        from app.core.comparison_engine import _fetch_doc_chunks
        text = await _fetch_doc_chunks(source_file, workspace_id)
        return text[:_CHUNK_CHARS]
    except Exception as e:
        logger.warning(f"Could not fetch chunks for {source_file}: {e}")
        return f"[Could not retrieve text from {source_file}]"


def _short(path: str) -> str:
    return path.split("/")[-1].split("\\")[-1]


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/detect", response_model=DiscrepancyResponse)
async def detect_discrepancies(
    req: DiscrepancyRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> DiscrepancyResponse:
    corr_id = generate_correlation_id("disc")
    workspace_id = req.workspace_id or user.workspace_id

    text_a, text_b = await asyncio.gather(
        _fetch(req.doc_a, workspace_id),
        _fetch(req.doc_b, workspace_id),
    )

    prompt = _DISCREPANCY_PROMPT.format(
        name_a=_short(req.doc_a),
        text_a=text_a,
        name_b=_short(req.doc_b),
        text_b=text_b,
    )

    try:
        from app.core.vision_llm import get_vision_llm
        llm = get_vision_llm()
        loop = asyncio.get_running_loop()
        raw = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: llm.invoke(prompt)),
            timeout=_LLM_TIMEOUT,
        )
        content = raw.content if hasattr(raw, "content") else str(raw)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Discrepancy detection timed out.")
    except Exception as e:
        logger.error(f"[{corr_id}] Discrepancy LLM failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Discrepancy detection failed.")

    # Parse JSON — extract array even if LLM wraps in markdown
    import json, re
    try:
        # strip markdown code fences if present
        clean = re.sub(r"```(?:json)?", "", content).strip().rstrip("`").strip()
        # find the first [ ... ] block
        m = re.search(r"\[.*\]", clean, re.DOTALL)
        items_raw = json.loads(m.group(0)) if m else []
    except Exception:
        items_raw = []

    items = []
    for obj in items_raw:
        try:
            items.append(DiscrepancyItem(
                field=str(obj.get("field", "Unknown")),
                doc_a_value=str(obj.get("doc_a_value", "")),
                doc_b_value=str(obj.get("doc_b_value", "")),
                severity=str(obj.get("severity", "medium")).lower(),
                note=str(obj.get("note", "")),
            ))
        except Exception:
            continue

    high = sum(1 for i in items if i.severity == "high")

    return DiscrepancyResponse(
        discrepancies=items,
        doc_a=req.doc_a,
        doc_b=req.doc_b,
        total=len(items),
        high_count=high,
        correlation_id=corr_id,
    )
