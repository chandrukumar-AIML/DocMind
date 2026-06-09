# backend/app/domains/logistics/anomaly_detector.py
# DVMELTSS-FIX: V - Validate, E - Error handling, M - Modular, S - Scalability

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Final, Optional

from .invoice_extractor import ExtractedInvoice

logger = logging.getLogger(__name__)


@dataclass
class Anomaly:
    """A detected invoice anomaly."""

    anomaly_type: str  # duplicate | amount_deviation | missing_po | date_anomaly
    severity: str  # critical | high | medium | low
    description: str
    invoice_ref: str
    details: dict = field(default_factory=dict)
    correlation_id: str = ""  # FIXED: Added for tracing


class AnomalyDetector:
    """
    Detects invoice anomalies:
    1. Duplicate invoices (same number or same vendor+amount+date)
    2. Amount deviations > threshold vs PO or expected range
    3. Missing PO references
    4. Date anomalies (due date before invoice date, very old invoices)
    5. Round-number fraud indicators
    """

    AMOUNT_DEVIATION_THRESHOLD: Final = 0.10  # 10% deviation triggers alert
    ROUND_NUMBER_THRESHOLD: Final = 1000  # amounts >= this that are exact multiples of 100

    def __init__(self):
        self._invoice_history: list[ExtractedInvoice] = []

    def add_to_history(self, invoice: ExtractedInvoice):
        """Add invoice to history for duplicate detection."""
        self._invoice_history.append(invoice)

    def detect(
        self,
        invoice: ExtractedInvoice,
        expected_amount: Optional[float] = None,
        po_amounts: Optional[dict[str, float]] = None,
        correlation_id: Optional[str] = None,  # FIXED: Added param
    ) -> list[Anomaly]:
        """
        Detect anomalies in an invoice.

        Args:
            invoice: newly extracted invoice
            expected_amount: expected amount from PO or contract (optional)
            po_amounts: {po_number: amount} lookup table (optional)
            correlation_id: Request ID for distributed tracing
        """
        corr_id = correlation_id or invoice.correlation_id or "logistics_unknown"
        anomalies: list[Anomaly] = []

        # 1. Duplicate detection
        anomalies.extend(self._check_duplicates(invoice, corr_id))

        # 2. Amount deviation
        if expected_amount is not None:
            anomalies.extend(self._check_amount_deviation(invoice, expected_amount, source="expected", corr_id=corr_id))

        # 3. PO amount matching
        if po_amounts and invoice.po_number:
            po_expected = po_amounts.get(invoice.po_number)
            if po_expected:
                anomalies.extend(self._check_amount_deviation(invoice, po_expected, source="PO", corr_id=corr_id))

        # 4. Missing PO reference
        if not invoice.po_number and invoice.total_amount > 1000:
            anomalies.append(
                Anomaly(
                    anomaly_type="missing_po",
                    severity="medium",
                    description=(
                        f"Invoice {invoice.invoice_number} for " f"${invoice.total_amount:,.2f} has no PO reference"
                    ),
                    invoice_ref=invoice.invoice_number or invoice.source_file,
                    correlation_id=corr_id,  # FIXED: Propagate correlation_id
                )
            )

        # 5. Date anomalies
        anomalies.extend(self._check_dates(invoice, corr_id))

        # 6. Round number indicators
        anomalies.extend(self._check_round_numbers(invoice, corr_id))

        if anomalies:
            logger.warning(
                f"[{corr_id}] Anomalies detected: {invoice.source_file} | "
                f"{len(anomalies)} anomalies | "
                f"types={[a.anomaly_type for a in anomalies]}"
            )
        return anomalies

    def _check_duplicates(self, invoice: ExtractedInvoice, corr_id: str) -> list[Anomaly]:
        """Check for exact duplicate invoice numbers or suspicious duplicates."""
        anomalies = []
        for hist in self._invoice_history:
            # Exact invoice number match
            if (
                invoice.invoice_number
                and hist.invoice_number
                and invoice.invoice_number == hist.invoice_number
                and hist.source_file != invoice.source_file
            ):
                anomalies.append(
                    Anomaly(
                        anomaly_type="duplicate",
                        severity="critical",
                        description=(
                            f"Duplicate invoice number {invoice.invoice_number} " f"found in {hist.source_file}"
                        ),
                        invoice_ref=invoice.invoice_number,
                        details={"duplicate_file": hist.source_file},
                        correlation_id=corr_id,
                    )
                )

            # Same vendor + same amount + same date (suspicious duplicate)
            elif (
                invoice.vendor_name
                and hist.vendor_name
                and invoice.vendor_name.lower() == hist.vendor_name.lower()
                and abs(invoice.total_amount - hist.total_amount) < 0.01
                and invoice.invoice_date == hist.invoice_date
                and hist.source_file != invoice.source_file
            ):
                anomalies.append(
                    Anomaly(
                        anomaly_type="duplicate",
                        severity="high",
                        description=(
                            f"Suspicious duplicate: same vendor ({invoice.vendor_name}), "
                            f"amount (${invoice.total_amount:,.2f}), "
                            f"date ({invoice.invoice_date})"
                        ),
                        invoice_ref=invoice.invoice_number or invoice.source_file,
                        details={"potential_duplicate": hist.source_file},
                        correlation_id=corr_id,
                    )
                )
        return anomalies

    def _check_amount_deviation(
        self,
        invoice: ExtractedInvoice,
        expected: float,
        source: str = "expected",
        corr_id: str = "unknown",
    ) -> list[Anomaly]:
        """Check if invoice amount deviates significantly from expected."""
        if expected <= 0 or invoice.total_amount <= 0:
            return []

        deviation = abs(invoice.total_amount - expected) / expected
        if deviation > self.AMOUNT_DEVIATION_THRESHOLD:
            severity = "critical" if deviation > 0.25 else "high"
            return [
                Anomaly(
                    anomaly_type="amount_deviation",
                    severity=severity,
                    description=(
                        f"Invoice amount ${invoice.total_amount:,.2f} deviates "
                        f"{deviation:.0%} from {source} amount ${expected:,.2f}"
                    ),
                    invoice_ref=invoice.invoice_number or invoice.source_file,
                    details={
                        "invoice_amount": invoice.total_amount,
                        "expected_amount": expected,
                        "deviation_pct": round(deviation * 100, 1),
                    },
                    correlation_id=corr_id,
                )
            ]
        return []

    def _check_dates(self, invoice: ExtractedInvoice, corr_id: str) -> list[Anomaly]:
        """Check for date-related anomalies with timezone-aware parsing."""
        anomalies = []
        try:
            today = datetime.now(timezone.utc)

            if invoice.invoice_date:
                # FIXED: Use timezone-aware parsing
                inv_date = datetime.fromisoformat(invoice.invoice_date.replace("Z", "+00:00"))
                if inv_date.tzinfo is None:
                    inv_date = inv_date.replace(tzinfo=timezone.utc)

                age_days = (today - inv_date).days

                # Invoice older than 90 days
                if age_days > 90:
                    anomalies.append(
                        Anomaly(
                            anomaly_type="date_anomaly",
                            severity="medium",
                            description=(f"Invoice dated {invoice.invoice_date} is " f"{age_days} days old"),
                            invoice_ref=invoice.invoice_number or invoice.source_file,
                            details={"age_days": age_days},
                            correlation_id=corr_id,
                        )
                    )

                # Due date before invoice date
                if invoice.due_date:
                    due = datetime.fromisoformat(invoice.due_date.replace("Z", "+00:00"))
                    if due.tzinfo is None:
                        due = due.replace(tzinfo=timezone.utc)
                    if due < inv_date:
                        anomalies.append(
                            Anomaly(
                                anomaly_type="date_anomaly",
                                severity="high",
                                description=(
                                    f"Due date {invoice.due_date} is before " f"invoice date {invoice.invoice_date}"
                                ),
                                invoice_ref=invoice.invoice_number or invoice.source_file,
                                correlation_id=corr_id,
                            )
                        )
        except Exception as e:
            logger.debug(f"[{corr_id}] Date check failed: {e}")
        return anomalies

    def _check_round_numbers(self, invoice: ExtractedInvoice, corr_id: str) -> list[Anomaly]:
        """Flag suspiciously round amounts (potential fraud indicator)."""
        anomalies = []
        total = invoice.total_amount

        if total >= self.ROUND_NUMBER_THRESHOLD and total % 100 == 0 and len(invoice.line_items) <= 1:
            anomalies.append(
                Anomaly(
                    anomaly_type="round_number",
                    severity="low",
                    description=(
                        f"Invoice total ${total:,.0f} is a round number "
                        f"with {len(invoice.line_items)} line item(s) — "
                        "verify line items are itemized correctly"
                    ),
                    invoice_ref=invoice.invoice_number or invoice.source_file,
                    correlation_id=corr_id,
                )
            )
        return anomalies


# DVMELTSS-M: Explicit module exports
__all__ = ["AnomalyDetector", "Anomaly"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
