"""Cited Answer PDF Export — Feature #6.

Converts a RAG answer (markdown + citations) to a clean PDF
using reportlab (pure-Python, no wkhtmltopdf needed).
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["export"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class Citation(BaseModel):
    source_file: str
    chunk_index: Optional[int] = None
    excerpt:     Optional[str] = None


class ExportPdfRequest(BaseModel):
    question:   str            = Field(..., max_length=1000)
    answer:     str            = Field(..., max_length=20000)
    citations:  list[Citation] = Field(default_factory=list)
    workspace_name: Optional[str] = None
    doc_title:  Optional[str]  = None


# ── PDF builder ───────────────────────────────────────────────────────────────

def _build_pdf(req: ExportPdfRequest, user_email: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, HRFlowable,
        Table, TableStyle, KeepTogether,
    )
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
    import re

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=22*mm, bottomMargin=22*mm,
        title="DocuMind AI — Cited Answer",
    )

    W = A4[0] - 40*mm   # usable width

    # ── Colour palette ────────────────────────────────────────────────────
    DARK   = colors.HexColor("#0f1117")
    TEAL   = colors.HexColor("#0d9488")
    VIOLET = colors.HexColor("#8b5cf6")
    GREY1  = colors.HexColor("#374151")
    GREY2  = colors.HexColor("#6b7280")
    GREY3  = colors.HexColor("#e5e7eb")
    WHITE  = colors.white
    AMBER  = colors.HexColor("#f59e0b")

    base = getSampleStyleSheet()

    def style(name, **kw):
        s = ParagraphStyle(name, parent=base["Normal"], **kw)
        return s

    S_HEADING  = style("Heading",  fontSize=15, textColor=DARK,   fontName="Helvetica-Bold",  leading=20, spaceAfter=4)
    S_SUBHEAD  = style("Subhead",  fontSize=9,  textColor=GREY2,  fontName="Helvetica",       leading=14)
    S_SECTION  = style("Section",  fontSize=10, textColor=TEAL,   fontName="Helvetica-Bold",  leading=14, spaceBefore=10, spaceAfter=3)
    S_BODY     = style("Body",     fontSize=9,  textColor=DARK,   fontName="Helvetica",       leading=14, spaceAfter=4)
    S_BOLD     = style("Bold",     fontSize=9,  textColor=DARK,   fontName="Helvetica-Bold",  leading=14)
    S_CITE     = style("Cite",     fontSize=8,  textColor=GREY1,  fontName="Helvetica",       leading=12, leftIndent=6)
    S_EXCERPT  = style("Excerpt",  fontSize=8,  textColor=GREY2,  fontName="Helvetica-Oblique", leading=12, leftIndent=12)
    S_FOOTER   = style("Footer",   fontSize=7,  textColor=GREY2,  fontName="Helvetica",       leading=10, alignment=TA_CENTER)

    def short(p):
        return (p or "").split("/")[-1].split("\\")[-1]

    # ── Convert markdown to reportlab paragraphs ──────────────────────────
    def md_to_paras(text):
        paras = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                paras.append(Spacer(1, 3))
                continue
            # headings
            if stripped.startswith("### "):
                paras.append(Paragraph(stripped[4:], S_SECTION))
            elif stripped.startswith("## "):
                paras.append(Paragraph(stripped[3:], S_SECTION))
            elif stripped.startswith("# "):
                paras.append(Paragraph(stripped[2:], S_HEADING))
            # bullets
            elif stripped.startswith(("- ", "* ")):
                txt = stripped[2:]
                txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
                paras.append(Paragraph(f"• {txt}", S_BODY))
            # numbered list
            elif re.match(r"^\d+\. ", stripped):
                txt = re.sub(r"^\d+\. ", "", stripped)
                txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
                paras.append(Paragraph(f"  {txt}", S_BODY))
            # horizontal rule
            elif stripped.startswith("---"):
                paras.append(HRFlowable(width="100%", thickness=0.5, color=GREY3))
            else:
                txt = stripped
                txt = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", txt)
                txt = re.sub(r"\*(.+?)\*",     r"<i>\1</i>", txt)
                txt = re.sub(r"`(.+?)`",        r"<font name='Courier'>\1</font>", txt)
                paras.append(Paragraph(txt, S_BODY))
        return paras

    # ── Page header / footer callback ─────────────────────────────────────
    def _page(canvas, doc):
        canvas.saveState()
        # header strip
        canvas.setFillColor(DARK)
        canvas.rect(0, A4[1]-14*mm, A4[0], 14*mm, fill=1, stroke=0)
        canvas.setFillColor(TEAL)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(20*mm, A4[1]-9*mm, "DocuMind AI")
        canvas.setFillColor(WHITE)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0]-20*mm, A4[1]-9*mm, f"Page {doc.page}  ·  {datetime.now().strftime('%d %b %Y')}")
        # footer
        canvas.setFillColor(GREY2)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(A4[0]/2, 12*mm, "AI-generated output — verify all figures before use  ·  DocuMind AI")
        canvas.restoreState()

    # ── Build story ───────────────────────────────────────────────────────
    story = []

    # Title block
    ws_label = req.workspace_name or "Default Workspace"
    story.append(Spacer(1, 4))
    story.append(Paragraph("Cited Answer Export", S_HEADING))
    story.append(Paragraph(f"{ws_label}  ·  {datetime.now().strftime('%d %B %Y, %I:%M %p')}", S_SUBHEAD))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1, color=TEAL))
    story.append(Spacer(1, 6))

    # Question
    story.append(Paragraph("Question", S_SECTION))
    story.append(Paragraph(req.question, S_BOLD))
    story.append(Spacer(1, 6))

    # Answer
    story.append(Paragraph("Answer", S_SECTION))
    story.extend(md_to_paras(req.answer))
    story.append(Spacer(1, 8))

    # Citations
    if req.citations:
        story.append(HRFlowable(width="100%", thickness=0.5, color=GREY3))
        story.append(Spacer(1, 4))
        story.append(Paragraph("Source Citations", S_SECTION))
        for idx, c in enumerate(req.citations, 1):
            story.append(Paragraph(f"[{idx}]  {short(c.source_file)}", S_CITE))
            if c.excerpt:
                excerpt = c.excerpt[:300] + ("…" if len(c.excerpt) > 300 else "")
                story.append(Paragraph(f'"{excerpt}"', S_EXCERPT))
            story.append(Spacer(1, 3))

    # Disclaimer
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY3))
    story.append(Spacer(1, 4))
    disc_style = style("Disc", fontSize=7.5, textColor=GREY2, fontName="Helvetica-Oblique", leading=11, alignment=TA_CENTER)
    story.append(Paragraph(
        "This document was generated by DocuMind AI. All figures, dates, and legal citations must be "
        "independently verified by a qualified Chartered Accountant before use in any filing, reply, or legal proceeding.",
        disc_style,
    ))

    doc.build(story, onFirstPage=_page, onLaterPages=_page)
    return buf.getvalue()


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/pdf")
async def export_pdf(
    req: ExportPdfRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Generate a PDF of a cited answer and stream it back."""
    import asyncio
    loop = asyncio.get_running_loop()
    pdf_bytes = await loop.run_in_executor(None, lambda: _build_pdf(req, user.email))

    filename = f"documind-answer-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
