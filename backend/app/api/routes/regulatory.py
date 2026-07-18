"""Built-in Regulatory Knowledge — Feature #9.

Provides a curated, hardcoded knowledge base of Indian CA regulations,
GST sections, IT Act provisions, and penalty tables. Returns relevant
snippets based on keyword search — no LLM needed, instant response.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser

router = APIRouter(prefix="/regulatory", tags=["regulatory"])

# ── Knowledge base ────────────────────────────────────────────────────────────
# Format: {id, category, title, section, content, keywords}

KB: list[dict] = [
    # GST
    {
        "id": "gst-16-4",
        "category": "GST",
        "title": "ITC Claim Time Limit",
        "section": "Section 16(4) CGST Act",
        "content": "Input Tax Credit cannot be claimed after the earlier of: (a) due date of filing GSTR-3B for October of next FY, or (b) date of filing annual return. Late ITC claims attract reversal with interest @ 18% p.a.",
        "keywords": ["itc", "input tax credit", "16(4)", "claim", "time limit", "reversal"],
    },
    {
        "id": "gst-drc-03",
        "category": "GST",
        "title": "Voluntary Tax Payment — DRC-03",
        "section": "Rule 142(2) CGST Rules",
        "content": "A taxpayer can voluntarily pay tax shortfall using Form DRC-03. This can be done during audit, inspection, or suo moto. Payment via DRC-03 reduces the demand and helps avoid penalty proceedings under Sections 73/74.",
        "keywords": ["drc-03", "voluntary", "payment", "demand", "penalty", "suo moto"],
    },
    {
        "id": "gst-73",
        "category": "GST",
        "title": "Show Cause Notice — Non-Fraud",
        "section": "Section 73 CGST Act",
        "content": "Section 73 applies to tax shortfall WITHOUT fraud/misrepresentation. Time limit: 3 years from due date of annual return. Penalty: 10% of tax or ₹10,000 whichever is higher. If paid before SCN: no penalty. If paid after SCN but before order: 25% of tax.",
        "keywords": ["section 73", "scn", "show cause", "non-fraud", "penalty", "73"],
    },
    {
        "id": "gst-74",
        "category": "GST",
        "title": "Show Cause Notice — Fraud",
        "section": "Section 74 CGST Act",
        "content": "Section 74 applies when tax is evaded through FRAUD, wilful misrepresentation, or suppression. Time limit: 5 years. Penalty: equal to tax evaded (100%). If paid before SCN: 15% penalty. If paid after order: 100% penalty. Prosecution possible above ₹5 Cr.",
        "keywords": ["section 74", "fraud", "wilful", "suppression", "prosecution", "74"],
    },
    {
        "id": "gst-16-2",
        "category": "GST",
        "title": "ITC Eligibility Conditions",
        "section": "Section 16(2) CGST Act",
        "content": "Four conditions for valid ITC: (1) Registered taxpayer, (2) Tax invoice/debit note received, (3) Goods/services actually received, (4) Tax actually paid to govt by supplier. ITC reversal required if payment not made to supplier within 180 days.",
        "keywords": ["itc", "eligibility", "16(2)", "supplier", "180 days", "conditions"],
    },
    {
        "id": "gst-gstr9",
        "category": "GST",
        "title": "GSTR-9 Annual Return",
        "section": "Section 44 CGST Act",
        "content": "GSTR-9 is mandatory for taxpayers with turnover above ₹2 Cr. Due date: 31 December of subsequent FY. Late fee: ₹200/day (₹100 CGST + ₹100 SGST), max 0.25% of turnover. Taxpayers below ₹2 Cr exempt from GSTR-9C (reconciliation).",
        "keywords": ["gstr-9", "annual return", "section 44", "late fee", "gstr-9c", "reconciliation"],
    },
    # Income Tax
    {
        "id": "it-271aac",
        "category": "Income Tax",
        "title": "Unexplained Cash Credits — Penalty",
        "section": "Section 68 / 271AAC IT Act",
        "content": "Unexplained cash credits under Sec 68 are taxed at flat 60% + surcharge 25% = effective 83%. Additional penalty u/s 271AAC: 10% of tax. No deduction/exemption allowed. Often invoked after demonetisation deposits or large unexplained bank credits.",
        "keywords": ["section 68", "271aac", "unexplained", "cash credit", "60%", "penalty", "demonetisation"],
    },
    {
        "id": "it-234a",
        "category": "Income Tax",
        "title": "Interest on Late Filing — 234A",
        "section": "Section 234A IT Act",
        "content": "Interest @ 1% per month (simple) on tax payable if ITR filed after due date. Calculated from due date to actual date of filing. Minimum 1 month charged even for partial months.",
        "keywords": ["234a", "interest", "late filing", "itr", "1%"],
    },
    {
        "id": "it-234b",
        "category": "Income Tax",
        "title": "Interest on Shortfall in Advance Tax — 234B",
        "section": "Section 234B IT Act",
        "content": "Interest @ 1%/month if advance tax paid < 90% of assessed tax. Calculated from 1 April of assessment year to date of actual payment. Applies if self-assessment tax is outstanding.",
        "keywords": ["234b", "advance tax", "90%", "interest", "shortfall"],
    },
    {
        "id": "it-271b",
        "category": "Income Tax",
        "title": "Penalty for Audit Report Non-Filing",
        "section": "Section 271B IT Act",
        "content": "Penalty for failure to get accounts audited u/s 44AB or furnish audit report: 0.5% of total turnover or ₹1,50,000 whichever is lower. No penalty if reasonable cause is established.",
        "keywords": ["271b", "audit", "44ab", "penalty", "turnover", "tax audit"],
    },
    {
        "id": "it-143-1",
        "category": "Income Tax",
        "title": "Intimation under Section 143(1)",
        "section": "Section 143(1) IT Act",
        "content": "Intimation u/s 143(1) is a computer-generated summary after ITR processing. It may show demand (arithmetic/tax mismatch) or refund. Not a scrutiny notice. Time limit to respond: 30 days from issue. Rectify via u/s 154 if error in intimation.",
        "keywords": ["143(1)", "intimation", "demand", "refund", "scrutiny", "rectification", "154"],
    },
    {
        "id": "it-148",
        "category": "Income Tax",
        "title": "Reopening of Assessment — 148",
        "section": "Section 148 IT Act (as amended FA 2021)",
        "content": "Notice u/s 148 for reopening assessment. New scheme (post FA 2021): income escaped > ₹50 lakh — 10 years; others — 3 years. Mandatory show cause notice before issue. CBDT approval required above ₹1 lakh escaped income. Must respond within 3 months.",
        "keywords": ["148", "reopening", "escaped income", "reassessment", "10 years", "50 lakh"],
    },
    # TDS
    {
        "id": "tds-194q",
        "category": "TDS",
        "title": "TDS on Purchase of Goods — 194Q",
        "section": "Section 194Q IT Act",
        "content": "Applicable from 1 July 2021. Buyer with turnover > ₹10 Cr must deduct TDS @ 0.1% on purchase from seller exceeding ₹50 lakh in FY. If seller's PAN not available: TDS @ 5%. Not applicable on transactions covered by TCS u/s 206C.",
        "keywords": ["194q", "tds", "purchase", "goods", "0.1%", "50 lakh", "10 crore"],
    },
    {
        "id": "tds-default",
        "category": "TDS",
        "title": "Consequences of TDS Default",
        "section": "Section 201 / 271C IT Act",
        "content": "Failure to deduct TDS: treated as assessee-in-default, interest @ 1% p.m. from date tax was deductible to deduction date. Failure to deposit deducted TDS: interest @ 1.5% p.m. Penalty u/s 271C: equal to TDS amount. Prosecution u/s 276B for wilful default.",
        "keywords": ["tds default", "section 201", "271c", "1.5%", "interest", "assessee in default"],
    },
    # ROC / Companies Act
    {
        "id": "roc-137",
        "category": "ROC",
        "title": "Filing of Financial Statements — AOC-4",
        "section": "Section 137 Companies Act 2013",
        "content": "Every company must file financial statements (AOC-4) within 30 days of AGM (or 30 Oct for OPCs). Late filing fee: ₹100/day, no upper limit. Directors can be disqualified u/s 164(2) if AOC-4/MGT-7 not filed for 3 consecutive years.",
        "keywords": ["aoc-4", "section 137", "financial statements", "agm", "late fee", "164", "disqualification"],
    },
    {
        "id": "roc-mgt7",
        "category": "ROC",
        "title": "Annual Return — MGT-7",
        "section": "Section 92 Companies Act 2013",
        "content": "MGT-7/MGT-7A must be filed within 60 days of AGM. Due date: generally 29 November. Late fee: ₹100/day. CS certification required for companies with paid-up capital ≥ ₹10 Cr or turnover ≥ ₹50 Cr.",
        "keywords": ["mgt-7", "annual return", "section 92", "agm", "company secretary", "late fee"],
    },
    # PF / ESI
    {
        "id": "pf-interest",
        "category": "PF",
        "title": "PF Late Deposit — Interest & Damages",
        "section": "Section 7Q / 14B EPF Act",
        "content": "Interest u/s 7Q: 12% p.a. on delayed PF deposit. Damages u/s 14B: 5% p.a. (delay up to 2 months), 10% (2-4 months), 15% (4-6 months), 25% (above 6 months). Both are payable over and above the PF amount.",
        "keywords": ["pf", "provident fund", "7q", "14b", "interest", "damages", "delay"],
    },
]

# ── Precompute keyword index ──────────────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))

_INDEXED = [(item, _tokenize(" ".join(item["keywords"]) + " " + item["title"] + " " + item["section"])) for item in KB]


def _search(query: str, limit: int = 5) -> list[dict]:
    q_tokens = _tokenize(query)
    scored = []
    for item, tokens in _INDEXED:
        score = len(q_tokens & tokens)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda x: -x[0])
    return [item for _, item in scored[:limit]]


# ── Schemas ───────────────────────────────────────────────────────────────────

class RegEntry(BaseModel):
    id:       str
    category: str
    title:    str
    section:  str
    content:  str

class RegSearchResponse(BaseModel):
    results: list[RegEntry]
    query:   str
    total:   int

class RegListResponse(BaseModel):
    categories: dict[str, list[RegEntry]]
    total: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/search", response_model=RegSearchResponse)
async def search_regulatory(
    q: str,
    limit: int = 5,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Keyword search across the CA regulatory knowledge base."""
    results = _search(q, limit=min(limit, 10))
    return RegSearchResponse(
        results=[RegEntry(**r) for r in results],
        query=q,
        total=len(results),
    )


@router.get("/list", response_model=RegListResponse)
async def list_regulatory(
    category: Optional[str] = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all regulatory entries, optionally filtered by category."""
    filtered = [r for r in KB if not category or r["category"].lower() == category.lower()]
    by_cat: dict[str, list] = {}
    for r in filtered:
        by_cat.setdefault(r["category"], []).append(RegEntry(**r))
    return RegListResponse(categories=by_cat, total=len(filtered))
