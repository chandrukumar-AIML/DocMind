# backend/app/domains/logistics/po_matcher.py
# DVMELTSS-FIX: V - Validate, M - Modular, S - Scalability

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, List, Optional

from .invoice_extractor import ExtractedInvoice, LineItem

logger = logging.getLogger(__name__)

# Threshold for fuzzy matching descriptions (0.0 - 1.0)
_DESCRIPTION_MATCH_THRESHOLD: Final = 0.85
_AMOUNT_TOLERANCE: Final = 0.05  # 5% variance allowed for unit price


@dataclass(frozen=True)
class MatchResult:
    invoice_item: LineItem
    po_item: dict  # Assumed structure: {"description": str, "qty": float, "unit_price": float}
    confidence: float
    match_type: str  # exact | fuzzy | partial
    is_matched: bool
    discrepancy: Optional[str] = None
    correlation_id: str = ""  # FIXED: Added for tracing


class POMatcher:
    """
    Matches extracted invoice items to Purchase Order (PO) data.

    Features:
    - Exact matching on SKU/Part numbers
    - Fuzzy matching on descriptions using Levenshtein distance
    - Price tolerance checks
    """

    def match(
        self,
        invoice: ExtractedInvoice,
        po_data: List[dict],  # List of PO line item dicts
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> List[MatchResult]:
        """
        Match invoice items against PO items.

        Args:
            invoice: Extracted invoice data.
            po_data: List of dicts from PO system.
            correlation_id: Request ID for distributed tracing
        """
        corr_id = correlation_id or invoice.correlation_id or "logistics_unknown"
        results = []
        used_po_indices = set()

        for inv_item in invoice.line_items:
            best_match = MatchResult(
                invoice_item=inv_item,
                po_item={},
                confidence=0.0,
                match_type="none",
                is_matched=False,
                discrepancy="No matching PO item found",
                correlation_id=corr_id,
            )

            for idx, po_item in enumerate(po_data):
                if idx in used_po_indices:
                    continue

                score, match_type = self._score_match(inv_item, po_item)

                if score > best_match.confidence:
                    best_match = MatchResult(
                        invoice_item=inv_item,
                        po_item=po_item,
                        confidence=score,
                        match_type=match_type,
                        is_matched=True,
                        correlation_id=corr_id,
                    )
                    # Check for price discrepancy
                    po_price = po_item.get("unit_price", 0)
                    if po_price > 0 and abs(inv_item.unit_price - po_price) > _AMOUNT_TOLERANCE * po_price:
                        best_match = MatchResult(
                            **{k: v for k, v in best_match.__dict__.items() if k != "__dataclass_fields__"},
                            discrepancy=f"Price mismatch: Invoice ${inv_item.unit_price:.2f} vs PO ${po_price:.2f}",
                        )

            if best_match.is_matched:
                # Find the index of the matched PO item to mark used
                for idx, po in enumerate(po_data):
                    if po == best_match.po_item:
                        used_po_indices.add(idx)
                        break

            results.append(best_match)

        return results

    def _score_match(self, inv_item: LineItem, po_item: dict) -> tuple[float, str]:
        """Score the match between an invoice item and a PO item."""
        inv_desc = inv_item.description.lower()
        po_desc = po_item.get("description", "").lower()

        # 1. Exact SKU match (if available in metadata/desc)
        sku_match = self._extract_sku(inv_desc) == self._extract_sku(po_desc)
        if sku_match:
            return 1.0, "exact"

        # 2. Fuzzy description match (Simple token overlap for speed)
        overlap = len(set(inv_desc.split()) & set(po_desc.split()))
        total_words = max(len(set(inv_desc.split())), len(set(po_desc.split())), 1)
        text_score = overlap / total_words

        if text_score >= _DESCRIPTION_MATCH_THRESHOLD:
            return text_score, "fuzzy"

        return 0.0, "none"

    def _extract_sku(self, text: str) -> str:
        """Simple regex to extract potential SKU/Part numbers."""
        match = re.search(r"(?:SKU|Part|PN|Item)#?\s*([A-Z0-9\-]{3,})", text, re.I)
        return match.group(1).upper() if match else ""


# DVMELTSS-M: Explicit module exports
__all__ = ["POMatcher", "MatchResult"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
