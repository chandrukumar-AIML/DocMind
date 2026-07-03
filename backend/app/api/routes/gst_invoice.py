# backend/app/api/routes/gst_invoice.py
"""
GST invoice generation for Indian B2B clients.

POST /gst-invoice/generate  — returns a PDF invoice (application/pdf)
GET  /gst-invoice/preview   — returns the invoice as HTML for browser preview

GST rules applied:
  - 18% GST on SaaS services in India (SAC code 998314)
  - B2B: if buyer GSTIN provided, IGST (inter-state) or CGST+SGST (intra-state TN)
  - Terazion's state: Tamil Nadu (state code 33)
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, Field

from app.auth.dependencies import AuthenticatedUser, require_workspace_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/gst-invoice", tags=["gst-invoice"])

# Terazion company details
SELLER = {
    "name":    "Terazion Services",
    "address": "Chennai, Tamil Nadu — 600001",
    "state":   "Tamil Nadu",
    "state_code": "33",
    "gstin":   "33XXXXX0000X1ZX",   # ← replace with real GSTIN after registration
    "pan":     "XXXXX0000X",         # ← replace with real PAN
    "email":   "terazionservices@gmail.com",
    "sac_code": "998314",            # IT design and development services
}
GST_RATE = 0.18   # 18%


class InvoiceRequest(BaseModel):
    plan:              str   = Field(..., description="Plan purchased: starter | pro | enterprise")
    amount_inr:        float = Field(..., gt=0, description="Base amount in INR (excl. GST)")
    buyer_name:        str   = Field(..., min_length=1, max_length=200)
    buyer_address:     str   = Field(..., min_length=1, max_length=500)
    buyer_state:       str   = Field(..., description="Buyer's state name")
    buyer_state_code:  str   = Field(..., min_length=2, max_length=2, description="2-digit state code")
    buyer_gstin:       Optional[str] = Field(default=None, max_length=15)
    buyer_email:       Optional[str] = Field(default=None)
    invoice_number:    Optional[str] = Field(default=None, description="Auto-generated if not provided")
    period:            Optional[str] = Field(default=None, description="e.g. July 2026")


def _compute_gst(amount: float, seller_state_code: str, buyer_state_code: str) -> dict:
    """Returns GST breakdown based on inter vs intra-state."""
    gst_amount = round(amount * GST_RATE, 2)
    total      = round(amount + gst_amount, 2)

    if seller_state_code == buyer_state_code:
        # Intra-state: CGST + SGST (9% each)
        half = round(gst_amount / 2, 2)
        return {
            "cgst_rate": 9, "cgst": half,
            "sgst_rate": 9, "sgst": half,
            "igst_rate": 0, "igst": 0,
            "total_gst": gst_amount,
            "total":     total,
        }
    else:
        # Inter-state: IGST (18%)
        return {
            "cgst_rate": 0, "cgst": 0,
            "sgst_rate": 0, "sgst": 0,
            "igst_rate": 18, "igst": gst_amount,
            "total_gst": gst_amount,
            "total":     total,
        }


def _render_html(req: InvoiceRequest, inv_no: str, gst: dict, date_str: str) -> str:
    period_line = req.period or date_str[:7]
    cgst_row = f"<tr><td>CGST @ {gst['cgst_rate']}%</td><td></td><td>₹{gst['cgst']:,.2f}</td></tr>" if gst["cgst"] else ""
    sgst_row = f"<tr><td>SGST @ {gst['sgst_rate']}%</td><td></td><td>₹{gst['sgst']:,.2f}</td></tr>" if gst["sgst"] else ""
    igst_row = f"<tr><td>IGST @ {gst['igst_rate']}%</td><td></td><td>₹{gst['igst']:,.2f}</td></tr>" if gst["igst"] else ""
    gstin_row = f"<p><strong>GSTIN:</strong> {req.buyer_gstin}</p>" if req.buyer_gstin else ""

    plan_label = {"starter": "DocuMind AI — Starter Plan", "pro": "DocuMind AI — Pro Plan", "enterprise": "DocuMind AI — Enterprise Plan"}.get(req.plan, f"DocuMind AI — {req.plan.title()} Plan")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Tax Invoice — {inv_no}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Arial, sans-serif; font-size: 13px; color: #111; background: #fff; padding: 32px; }}
  h1 {{ font-size: 22px; font-weight: 700; margin-bottom: 4px; }}
  .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #0d9488; padding-bottom: 16px; margin-bottom: 20px; }}
  .logo {{ color: #0d9488; font-size: 20px; font-weight: 900; letter-spacing: -1px; }}
  .meta {{ text-align: right; color: #555; font-size: 12px; }}
  .parties {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-bottom: 20px; }}
  .party h3 {{ font-size: 11px; text-transform: uppercase; color: #888; margin-bottom: 8px; letter-spacing: .5px; }}
  .party p {{ margin-bottom: 3px; line-height: 1.5; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 16px; }}
  th {{ background: #0d9488; color: #fff; padding: 8px 12px; text-align: left; font-size: 12px; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #e5e7eb; }}
  .totals td {{ border: none; }}
  .totals tr:last-child td {{ font-weight: 700; font-size: 14px; border-top: 2px solid #0d9488; padding-top: 10px; }}
  .footer {{ margin-top: 32px; font-size: 11px; color: #888; border-top: 1px solid #e5e7eb; padding-top: 16px; }}
  .badge {{ display: inline-block; background: #0d948822; color: #0d9488; border: 1px solid #0d948844; padding: 2px 10px; border-radius: 99px; font-size: 11px; font-weight: 600; margin-bottom: 8px; }}
  @media print {{ body {{ padding: 0; }} }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="logo">DocuMind AI</div>
    <div style="color:#555;font-size:12px;margin-top:4px;">by Terazion Services</div>
  </div>
  <div class="meta">
    <div class="badge">TAX INVOICE</div>
    <p><strong>Invoice No:</strong> {inv_no}</p>
    <p><strong>Date:</strong> {date_str}</p>
    <p><strong>Period:</strong> {period_line}</p>
  </div>
</div>

<div class="parties">
  <div class="party">
    <h3>From (Seller)</h3>
    <p><strong>{SELLER["name"]}</strong></p>
    <p>{SELLER["address"]}</p>
    <p><strong>GSTIN:</strong> {SELLER["gstin"]}</p>
    <p><strong>PAN:</strong> {SELLER["pan"]}</p>
    <p>{SELLER["email"]}</p>
  </div>
  <div class="party">
    <h3>Bill To (Buyer)</h3>
    <p><strong>{req.buyer_name}</strong></p>
    <p>{req.buyer_address}</p>
    <p><strong>State:</strong> {req.buyer_state} ({req.buyer_state_code})</p>
    {gstin_row}
    {f"<p>{req.buyer_email}</p>" if req.buyer_email else ""}
  </div>
</div>

<table>
  <thead>
    <tr>
      <th style="width:50%">Description</th>
      <th>SAC Code</th>
      <th>HSN/SAC</th>
      <th>Amount (INR)</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>{plan_label}<br/><span style="color:#888;font-size:11px;">Subscription — {period_line}</span></td>
      <td>{SELLER["sac_code"]}</td>
      <td>Software as a Service</td>
      <td>₹{req.amount_inr:,.2f}</td>
    </tr>
  </tbody>
</table>

<table class="totals" style="max-width:360px;margin-left:auto;">
  <tbody>
    <tr><td>Subtotal</td><td></td><td>₹{req.amount_inr:,.2f}</td></tr>
    {cgst_row}
    {sgst_row}
    {igst_row}
    <tr><td>Total (INR)</td><td></td><td>₹{gst["total"]:,.2f}</td></tr>
  </tbody>
</table>

<div class="footer">
  <p><strong>Amount in words:</strong> Rupees {_amount_words(gst["total"])} Only</p>
  <br/>
  <p>This is a computer-generated invoice and does not require a physical signature.</p>
  <p>Payment received via Razorpay. For queries: {SELLER["email"]}</p>
  <p style="margin-top:8px;">Place of Supply: {req.buyer_state} | Nature of Service: IT Software Services (SAC {SELLER["sac_code"]})</p>
</div>

</body>
</html>"""


def _amount_words(amount: float) -> str:
    """Basic rupee amount to words (covers up to lakhs for typical SaaS invoices)."""
    import math
    n = int(math.floor(amount))
    ones  = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
             "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
             "Seventeen", "Eighteen", "Nineteen"]
    tens  = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def _below_hundred(x):
        if x < 20:    return ones[x]
        if x % 10 == 0: return tens[x // 10]
        return tens[x // 10] + " " + ones[x % 10]

    def _below_thousand(x):
        if x < 100: return _below_hundred(x)
        return ones[x // 100] + " Hundred" + (" " + _below_hundred(x % 100) if x % 100 else "")

    if n == 0: return "Zero"
    parts = []
    if n >= 100000:
        parts.append(_below_thousand(n // 100000) + " Lakh")
        n %= 100000
    if n >= 1000:
        parts.append(_below_thousand(n // 1000) + " Thousand")
        n %= 1000
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def _auto_invoice_number() -> str:
    now = datetime.now(timezone.utc)
    return f"DMK-{now.year}{now.month:02d}-{now.day:02d}{now.hour:02d}{now.minute:02d}"


@router.post("/generate")
async def generate_invoice(
    body: InvoiceRequest,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> Response:
    """Generate a GST invoice PDF (returns application/pdf)."""
    inv_no   = body.invoice_number or _auto_invoice_number()
    date_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    gst      = _compute_gst(body.amount_inr, SELLER["state_code"], body.buyer_state_code)
    html     = _render_html(body, inv_no, gst, date_str)

    try:
        import weasyprint  # optional dep — only needed for PDF output
        pdf = weasyprint.HTML(string=html).write_pdf()
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="invoice-{inv_no}.pdf"'},
        )
    except ImportError:
        # weasyprint not installed — return HTML so user can print-to-PDF
        logger.warning("weasyprint not installed — returning HTML invoice for browser print-to-PDF")
        return HTMLResponse(content=html + "<script>window.print()</script>")


@router.get("/preview")
async def preview_invoice(
    plan:             str,
    amount_inr:       float,
    buyer_name:       str,
    buyer_address:    str,
    buyer_state:      str,
    buyer_state_code: str,
    buyer_gstin:      Optional[str] = None,
    buyer_email:      Optional[str] = None,
    period:           Optional[str] = None,
    user: AuthenticatedUser = Depends(require_workspace_admin),
) -> HTMLResponse:
    """Preview invoice as HTML — user can print to PDF from browser."""
    body = InvoiceRequest(
        plan=plan, amount_inr=amount_inr,
        buyer_name=buyer_name, buyer_address=buyer_address,
        buyer_state=buyer_state, buyer_state_code=buyer_state_code,
        buyer_gstin=buyer_gstin, buyer_email=buyer_email, period=period,
    )
    inv_no   = _auto_invoice_number()
    date_str = datetime.now(timezone.utc).strftime("%d %B %Y")
    gst      = _compute_gst(body.amount_inr, SELLER["state_code"], body.buyer_state_code)
    html     = _render_html(body, inv_no, gst, date_str)
    return HTMLResponse(content=html)


__all__ = ["router"]
