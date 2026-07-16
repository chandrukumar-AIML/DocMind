"""
ITR / Financial Statement Analyzer for CA firms.

Extracts key figures from ITRs, balance sheets, P&L statements, Form 16,
26AS, TDS certificates. Computes ratios and flags discrepancies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from langchain_core.documents import Document

from app.core.domain_utils import get_domain_llm, safe_parse_llm_json

logger = logging.getLogger(__name__)

ITR_ANALYSIS_PROMPT = """You are an expert Chartered Accountant analyzing Indian financial documents.
Analyze the document and extract all financial figures and key information.

Return ONLY valid JSON:
{{
  "document_type": "itr | form_16 | form_26as | balance_sheet | profit_loss | tds_certificate | audit_report | bank_statement | unknown",
  "assessment_year": "AY YYYY-YY or null",
  "pan": "PAN number or null",
  "taxpayer_name": "name of taxpayer/company",
  "filing_status": "filed | pending | revised | null",

  "income_summary": {{
    "salary_income": 0.00,
    "business_income": 0.00,
    "capital_gains_short": 0.00,
    "capital_gains_long": 0.00,
    "house_property_income": 0.00,
    "other_income": 0.00,
    "gross_total_income": 0.00,
    "deductions_80c": 0.00,
    "deductions_other": 0.00,
    "net_taxable_income": 0.00
  }},

  "tax_computation": {{
    "tax_on_income": 0.00,
    "surcharge": 0.00,
    "health_education_cess": 0.00,
    "total_tax_liability": 0.00,
    "tds_deducted": 0.00,
    "advance_tax_paid": 0.00,
    "self_assessment_tax": 0.00,
    "tax_refund": 0.00,
    "tax_payable": 0.00
  }},

  "balance_sheet": {{
    "total_assets": 0.00,
    "fixed_assets": 0.00,
    "current_assets": 0.00,
    "total_liabilities": 0.00,
    "long_term_debt": 0.00,
    "current_liabilities": 0.00,
    "net_worth": 0.00,
    "share_capital": 0.00,
    "reserves_surplus": 0.00
  }},

  "profit_loss": {{
    "total_revenue": 0.00,
    "cost_of_goods_sold": 0.00,
    "gross_profit": 0.00,
    "operating_expenses": 0.00,
    "ebitda": 0.00,
    "depreciation": 0.00,
    "ebit": 0.00,
    "interest_expense": 0.00,
    "profit_before_tax": 0.00,
    "tax_expense": 0.00,
    "net_profit": 0.00
  }},

  "financial_ratios": {{
    "gross_profit_margin": 0.00,
    "net_profit_margin": 0.00,
    "current_ratio": 0.00,
    "debt_equity_ratio": 0.00,
    "return_on_equity": 0.00
  }},

  "tds_summary": {{
    "total_tds_deducted": 0.00,
    "tds_entries": [
      {{
        "deductor_name": "",
        "tan": "",
        "amount_paid": 0.00,
        "tds_deducted": 0.00,
        "section": "194A | 194C | 194J | 192 | etc"
      }}
    ]
  }},

  "key_observations": [
    "observation 1 — important finding for the CA",
    "observation 2"
  ],

  "red_flags": [
    {{
      "type": "tds_mismatch | income_mismatch | high_cash | loss_carry_forward | audit_risk",
      "severity": "high | medium | low",
      "description": "clear description"
    }}
  ],

  "summary": "3-4 sentence summary of key findings for the CA"
}}

Document text:
{text}"""


@dataclass
class ITRAnalysisResult:
    document_type: str = "unknown"
    assessment_year: Optional[str] = None
    pan: Optional[str] = None
    taxpayer_name: str = ""
    filing_status: Optional[str] = None
    income_summary: dict = field(default_factory=dict)
    tax_computation: dict = field(default_factory=dict)
    balance_sheet: dict = field(default_factory=dict)
    profit_loss: dict = field(default_factory=dict)
    financial_ratios: dict = field(default_factory=dict)
    tds_summary: dict = field(default_factory=dict)
    key_observations: list = field(default_factory=list)
    red_flags: list = field(default_factory=list)
    summary: str = ""
    correlation_id: Optional[str] = None


class ITRAnalyzer:
    """Analyzes ITR, balance sheets, P&L, Form 16/26AS using LLM."""

    async def analyze(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,
    ) -> ITRAnalysisResult:
        corr_id = correlation_id or "itr_analyze"

        full_text = "\n\n".join(
            doc.page_content for doc in chunks if doc.page_content.strip()
        )[:6000]

        if not full_text.strip():
            return ITRAnalysisResult(
                summary="No readable text found.",
                correlation_id=corr_id,
            )

        logger.info(f"[{corr_id}] ITR/Financial analysis: {len(chunks)} chunks")

        llm = get_domain_llm()
        prompt = ITR_ANALYSIS_PROMPT.format(text=full_text)

        try:
            response = await llm.ainvoke(prompt)
            content = response.content if hasattr(response, "content") else str(response)
            parsed = safe_parse_llm_json(content)
        except Exception as e:
            logger.error(f"[{corr_id}] LLM call failed: {e}")
            parsed = {}

        if not parsed:
            return ITRAnalysisResult(
                summary="Could not extract financial data from document.",
                correlation_id=corr_id,
            )

        return ITRAnalysisResult(
            document_type=parsed.get("document_type", "unknown"),
            assessment_year=parsed.get("assessment_year"),
            pan=parsed.get("pan"),
            taxpayer_name=parsed.get("taxpayer_name", ""),
            filing_status=parsed.get("filing_status"),
            income_summary=parsed.get("income_summary") or {},
            tax_computation=parsed.get("tax_computation") or {},
            balance_sheet=parsed.get("balance_sheet") or {},
            profit_loss=parsed.get("profit_loss") or {},
            financial_ratios=parsed.get("financial_ratios") or {},
            tds_summary=parsed.get("tds_summary") or {},
            key_observations=parsed.get("key_observations") or [],
            red_flags=parsed.get("red_flags") or [],
            summary=parsed.get("summary", ""),
            correlation_id=corr_id,
        )
