"""
GST Invoice & Notice Analyzer for CA firms.

Extracts GSTIN, CGST/SGST/IGST splits, HSN/SAC codes, ITC eligibility,
detects anomalies (rate mismatches, GSTIN format errors, missing fields).
Works on uploaded client documents — invoices, GSTR PDFs, GST notices.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional, Any, Final

from langchain_core.documents import Document

from app.core.domain_utils import get_domain_llm, safe_parse_llm_json

logger = logging.getLogger(__name__)

GSTIN_PATTERN: Final = re.compile(
    r"\b(\d{2}[A-Z]{5}\d{4}[A-Z]{1}[A-Z\d]{1}[Z]{1}[A-Z\d]{1})\b"
)
PAN_PATTERN: Final = re.compile(r"\b([A-Z]{5}\d{4}[A-Z]{1})\b")

GST_RATES: Final = {0, 0.1, 0.25, 3, 5, 12, 18, 28}

GST_ANALYSIS_PROMPT: Final = """You are an expert GST (Goods and Services Tax) analyst for India.
Analyze the document text below and extract all GST-related information.

Return ONLY valid JSON with this exact structure:
{{
  "document_type": "tax_invoice | gst_notice | gstr_return | credit_note | debit_note | unknown",
  "supplier": {{
    "name": "supplier/seller name",
    "gstin": "15-char GSTIN or null",
    "state": "state name",
    "state_code": "2-digit state code"
  }},
  "buyer": {{
    "name": "buyer/recipient name",
    "gstin": "15-char GSTIN or null",
    "state": "state name",
    "state_code": "2-digit state code"
  }},
  "invoice_details": {{
    "invoice_number": "invoice number or null",
    "invoice_date": "DD/MM/YYYY or null",
    "supply_type": "intra_state | inter_state | import | export | null",
    "place_of_supply": "state name"
  }},
  "line_items": [
    {{
      "description": "item/service description",
      "hsn_sac": "HSN or SAC code",
      "quantity": 0,
      "unit": "Nos/Kg/Ltrs etc",
      "taxable_value": 0.00,
      "gst_rate": 18,
      "cgst": 0.00,
      "sgst": 0.00,
      "igst": 0.00,
      "cess": 0.00,
      "total": 0.00,
      "itc_eligible": true,
      "reverse_charge": false
    }}
  ],
  "totals": {{
    "taxable_value": 0.00,
    "cgst": 0.00,
    "sgst": 0.00,
    "igst": 0.00,
    "cess": 0.00,
    "round_off": 0.00,
    "grand_total": 0.00,
    "total_itc_eligible": 0.00
  }},
  "anomalies": [
    {{
      "type": "gstin_invalid | rate_mismatch | missing_hsn | itc_blocked | reverse_charge_missed | tax_calculation_error",
      "severity": "high | medium | low",
      "description": "clear description of the issue",
      "field": "which field has the issue"
    }}
  ],
  "gst_notice_details": {{
    "notice_type": "SCN | DRC-01 | DRC-03 | ASMT-10 | ADJ-01 | null",
    "demand_amount": 0.00,
    "interest": 0.00,
    "penalty": 0.00,
    "total_demand": 0.00,
    "period_from": "MM/YYYY",
    "period_to": "MM/YYYY",
    "reply_due_date": "DD/MM/YYYY or null",
    "grounds": ["list of grounds of demand"]
  }},
  "compliance_status": "compliant | has_issues | critical_issues",
  "summary": "2-3 sentence plain-English summary of findings for the CA"
}}

Document text:
{text}"""


@dataclass
class GSTAnalysisResult:
    document_type: str = "unknown"
    supplier: dict = field(default_factory=dict)
    buyer: dict = field(default_factory=dict)
    invoice_details: dict = field(default_factory=dict)
    line_items: list = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    anomalies: list = field(default_factory=list)
    gst_notice_details: dict = field(default_factory=dict)
    compliance_status: str = "unknown"
    summary: str = ""
    raw_gstins: list = field(default_factory=list)
    correlation_id: Optional[str] = None


def _extract_gstins_regex(text: str) -> list[str]:
    """Fast regex pre-scan to find all GSTINs in the document."""
    return list(set(GSTIN_PATTERN.findall(text)))


def _validate_gstin(gstin: str) -> bool:
    """Basic GSTIN format validation (15 chars, correct structure)."""
    if not gstin or len(gstin) != 15:
        return False
    if not gstin[:2].isdigit():
        return False
    state_code = int(gstin[:2])
    if not (1 <= state_code <= 38):
        return False
    return bool(re.match(r"^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d][Z][A-Z\d]$", gstin))


def _auto_detect_anomalies(result: dict) -> list[dict]:
    """Rule-based anomaly detection to supplement LLM findings."""
    anomalies = list(result.get("anomalies", []))

    # Validate GSTINs
    supplier_gstin = (result.get("supplier") or {}).get("gstin")
    buyer_gstin = (result.get("buyer") or {}).get("gstin")
    for label, gstin in [("Supplier GSTIN", supplier_gstin), ("Buyer GSTIN", buyer_gstin)]:
        if gstin and not _validate_gstin(gstin):
            anomalies.append({
                "type": "gstin_invalid",
                "severity": "high",
                "description": f"{label} '{gstin}' has invalid format",
                "field": label.lower().replace(" ", "_"),
            })

    # Check tax calculation on line items
    for i, item in enumerate(result.get("line_items") or []):
        taxable = float(item.get("taxable_value") or 0)
        rate = float(item.get("gst_rate") or 0)
        cgst = float(item.get("cgst") or 0)
        sgst = float(item.get("sgst") or 0)
        igst = float(item.get("igst") or 0)
        if taxable > 0 and rate > 0:
            expected = round(taxable * rate / 100, 2)
            actual_tax = cgst + sgst + igst
            if abs(actual_tax - expected) > 1.0:
                anomalies.append({
                    "type": "tax_calculation_error",
                    "severity": "high",
                    "description": f"Line {i+1}: expected GST ₹{expected:.2f} at {rate}% but found ₹{actual_tax:.2f}",
                    "field": f"line_items[{i}]",
                })
        if not item.get("hsn_sac"):
            anomalies.append({
                "type": "missing_hsn",
                "severity": "medium",
                "description": f"Line {i+1} '{item.get('description','')[:40]}' has no HSN/SAC code",
                "field": f"line_items[{i}].hsn_sac",
            })

    return anomalies


class GSTAnalyzer:
    """Analyzes GST invoices and notices using LLM + rule-based checks."""

    async def analyze(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,
    ) -> GSTAnalysisResult:
        corr_id = correlation_id or "gst_analyze"

        # Combine chunks (limit to ~6000 chars to stay within context)
        full_text = "\n\n".join(
            doc.page_content for doc in chunks if doc.page_content.strip()
        )[:6000]

        if not full_text.strip():
            logger.warning(f"[{corr_id}] No text content for GST analysis")
            return GSTAnalysisResult(
                summary="No readable text found in document.",
                correlation_id=corr_id,
            )

        # Pre-extract GSTINs with regex
        raw_gstins = _extract_gstins_regex(full_text)
        logger.info(f"[{corr_id}] GST analysis: {len(chunks)} chunks, {len(raw_gstins)} GSTINs found")

        # LLM analysis
        llm = get_domain_llm()
        prompt = GST_ANALYSIS_PROMPT.format(text=full_text)

        try:
            response = await llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            parsed = safe_parse_llm_json(content)
        except Exception as e:
            logger.error(f"[{corr_id}] LLM call failed: {e}")
            parsed = {}

        if not parsed:
            return GSTAnalysisResult(
                raw_gstins=raw_gstins,
                summary=f"Could not parse GST data. Found {len(raw_gstins)} GSTIN(s): {', '.join(raw_gstins[:3])}",
                correlation_id=corr_id,
            )

        # Augment with rule-based anomaly detection
        parsed["anomalies"] = _auto_detect_anomalies(parsed)

        return GSTAnalysisResult(
            document_type=parsed.get("document_type", "unknown"),
            supplier=parsed.get("supplier") or {},
            buyer=parsed.get("buyer") or {},
            invoice_details=parsed.get("invoice_details") or {},
            line_items=parsed.get("line_items") or [],
            totals=parsed.get("totals") or {},
            anomalies=parsed.get("anomalies") or [],
            gst_notice_details=parsed.get("gst_notice_details") or {},
            compliance_status=parsed.get("compliance_status", "unknown"),
            summary=parsed.get("summary", ""),
            raw_gstins=raw_gstins,
            correlation_id=corr_id,
        )
