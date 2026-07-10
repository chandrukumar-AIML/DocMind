
"""
Logistics Domain Module: Invoicing, PO matching, and anomaly detection.

Public API:
    from app.domains.logistics import LogisticsRAGChain, InvoiceExtractor, AnomalyDetector
"""

from __future__ import annotations

__all__ = [
    "LogisticsRAGChain",
    "InvoiceExtractor",
    "AnomalyDetector",
    "POMatcher",
]

__version__ = "1.0.0"
__domain__ = "logistics"


def __getattr__(name: str):
    """Lazy imports to prevent circular dependencies."""
    if name == "LogisticsRAGChain":
        from .logistics_rag import LogisticsRAGChain

        return LogisticsRAGChain
    if name == "InvoiceExtractor":
        from .invoice_extractor import InvoiceExtractor

        return InvoiceExtractor
    if name == "AnomalyDetector":
        from .anomaly_detector import AnomalyDetector

        return AnomalyDetector
    if name == "POMatcher":
        from .po_matcher import POMatcher

        return POMatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _log_module_init() -> None:
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Logistics domain module loaded | version={__version__}")


_log_module_init()
