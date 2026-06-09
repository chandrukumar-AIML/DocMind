# backend/app/domains/logistics/invoice_extractor.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final, Optional

from langchain_core.documents import Document

# DVMELTSS-M: Import centralized utilities
from app.core.domain_utils import (
    build_domain_prompt,
    safe_parse_llm_json,
    get_domain_llm,
    validate_logistics_output,
    generate_domain_correlation_id,
)
from app.core.retry import retry_async, RetryConfig

logger = logging.getLogger(__name__)

INVOICE_PROMPT: Final = """Extract all invoice fields from this document.

Return ONLY valid JSON:
{{
  "invoice_number": "INV-2024-001",
  "invoice_date": "2024-01-15",
  "due_date": "2024-02-14",
  "vendor_name": "Acme Corp",
  "vendor_address": "123 Main St, SF CA 94102",
  "vendor_tax_id": "12-3456789",
  "buyer_name": "Client Inc",
  "buyer_address": "456 Oak Ave, NY NY 10001",
  "po_number": "PO-2024-042",
  "currency": "USD",
  "subtotal": 4250.00,
  "tax_amount": 340.00,
  "tax_rate": 0.08,
  "total_amount": 4590.00,
  "payment_terms": "Net 30",
  "line_items": [
    {{
      "description": "Software License",
      "quantity": 1,
      "unit_price": 3500.00,
      "total": 3500.00
    }}
  ],
  "notes": "any special instructions or notes"
}}

Use null for missing fields. amounts as numbers, not strings.

Document text:
{text}
"""


@dataclass
class LineItem:
    description: str
    quantity: float
    unit_price: float
    total: float


@dataclass
class ExtractedInvoice:
    """Fully structured invoice data."""

    source_file: str
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    vendor_tax_id: Optional[str] = None
    buyer_name: Optional[str] = None
    buyer_address: Optional[str] = None
    po_number: Optional[str] = None
    currency: str = "USD"
    subtotal: float = 0.0
    tax_amount: float = 0.0
    tax_rate: float = 0.0
    total_amount: float = 0.0
    payment_terms: Optional[str] = None
    line_items: list[LineItem] = field(default_factory=list)
    notes: Optional[str] = None
    extraction_confidence: float = 0.0
    correlation_id: str = ""  # FIXED: Added for tracing

    @property
    def is_complete(self) -> bool:
        """Check if all critical fields were extracted."""
        return all(
            [
                self.invoice_number,
                self.invoice_date,
                self.vendor_name,
                self.total_amount > 0,
            ]
        )

    def to_dict(self) -> dict:
        d = {
            "source_file": self.source_file,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "vendor_name": self.vendor_name,
            "buyer_name": self.buyer_name,
            "po_number": self.po_number,
            "currency": self.currency,
            "subtotal": self.subtotal,
            "tax_amount": self.tax_amount,
            "total_amount": self.total_amount,
            "payment_terms": self.payment_terms,
            "line_items": [
                {
                    "description": li.description,
                    "quantity": li.quantity,
                    "unit_price": li.unit_price,
                    "total": li.total,
                }
                for li in self.line_items
            ],
            "is_complete": self.is_complete,
        }
        return d


class InvoiceExtractor:
    """Extracts structured invoice data from document chunks."""

    def __init__(self, model: str = "gpt-4o"):
        # FIXED: Use centralized LLM pool
        self.llm = get_domain_llm(streaming=False, model_override=model)
        self._llm_retry = retry_async(
            config=RetryConfig(
                max_attempts=2,
                backoff_base=0.5,
                exceptions=(Exception,),
            )
        )

    async def extract(
        self,
        chunks: list[Document],
        source_file: str,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> ExtractedInvoice:
        """Extract invoice fields from document chunks."""
        corr_id = correlation_id or generate_domain_correlation_id("logistics")

        # Combine first 3 chunks (invoices are usually 1-2 pages)
        text = "\n\n".join(c.page_content for c in chunks[:3])

        # FIXED: Use centralized prompt builder
        prompt = build_domain_prompt(INVOICE_PROMPT, text=text[:3000])

        try:
            # FIXED: Apply retry + centralized JSON parsing
            response = await self._llm_retry(lambda: self.llm.ainvoke([{"role": "user", "content": prompt}]))
            data = safe_parse_llm_json(response.content, default={})

            is_valid, error = validate_logistics_output(data)
            if not is_valid:
                logger.warning(f"[{corr_id}] Invalid invoice output: {error}")
                return ExtractedInvoice(source_file=source_file, correlation_id=corr_id)

            line_items = [
                LineItem(
                    description=str(li.get("description", "")),
                    quantity=float(li.get("quantity", 1)),
                    unit_price=float(li.get("unit_price", 0)),
                    total=float(li.get("total", 0)),
                )
                for li in data.get("line_items", [])
                if li.get("description")
            ]

            # Compute confidence based on field completeness
            critical_fields = [
                "invoice_number",
                "invoice_date",
                "vendor_name",
                "total_amount",
            ]
            filled = sum(1 for f in critical_fields if data.get(f))
            confidence = filled / len(critical_fields)

            return ExtractedInvoice(
                source_file=source_file,
                invoice_number=data.get("invoice_number"),
                invoice_date=data.get("invoice_date"),
                due_date=data.get("due_date"),
                vendor_name=data.get("vendor_name"),
                vendor_address=data.get("vendor_address"),
                vendor_tax_id=data.get("vendor_tax_id"),
                buyer_name=data.get("buyer_name"),
                buyer_address=data.get("buyer_address"),
                po_number=data.get("po_number"),
                currency=str(data.get("currency", "USD")),
                subtotal=float(data.get("subtotal") or 0),
                tax_amount=float(data.get("tax_amount") or 0),
                tax_rate=float(data.get("tax_rate") or 0),
                total_amount=float(data.get("total_amount") or 0),
                payment_terms=data.get("payment_terms"),
                line_items=line_items,
                notes=data.get("notes"),
                extraction_confidence=confidence,
                correlation_id=corr_id,  # FIXED: Propagate correlation_id
            )
        except Exception as e:
            logger.error(f"[{corr_id}] Invoice extraction failed: {e}")
            return ExtractedInvoice(source_file=source_file, correlation_id=corr_id)


# DVMELTSS-M: Explicit module exports
__all__ = ["InvoiceExtractor", "ExtractedInvoice", "LineItem"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
