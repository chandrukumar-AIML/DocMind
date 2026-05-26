# backend/app/domains/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# ASCALE-FIX: S - Separation, C - Coupling

"""
DocuMind AI - Domain Specific Modules

Provides specialized RAG chains and extraction tools for:
- Legal: Contract analysis, clause extraction, risk scoring
- Logistics: Invoice extraction, PO matching, anomaly detection  
- Medical: Clinical NLP, ICD-10 coding, HIPAA-compliant redaction

Public API:
    from app.domains import LegalRAGChain, InvoiceExtractor, PIIRedactor

⚠️ HIPAA NOTICE: Medical domain requires PII redaction before external calls.
"""
from __future__ import annotations

# DVMELTSS-M: Explicit public API surface
__all__ = [
    # Legal
    "LegalRAGChain", "ClauseExtractor", "RiskScorer",
    # Logistics  
    "LogisticsRAGChain", "InvoiceExtractor", "AnomalyDetector", "POMatcher",
    # Medical
    "MedicalRAGChain", "DrugInteractionChecker", "ICD10Extractor", "PIIRedactor",
]

# ASCALE-S: Module metadata
__version__ = "1.0.0"
__description__ = "Domain-specific AI modules for DocuMind"


def __getattr__(name: str):
    """Lazy imports to prevent circular dependencies and reduce startup time."""
    # Legal domain
    if name == "LegalRAGChain":
        from .legal.legal_rag import LegalRAGChain
        return LegalRAGChain
    if name == "ClauseExtractor":
        from .legal.clause_extractor import ClauseExtractor
        return ClauseExtractor
    if name == "RiskScorer":
        from .legal.risk_scorer import RiskScorer
        return RiskScorer
    
    # Logistics domain
    if name == "LogisticsRAGChain":
        from .logistics.logistics_rag import LogisticsRAGChain
        return LogisticsRAGChain
    if name == "InvoiceExtractor":
        from .logistics.invoice_extractor import InvoiceExtractor
        return InvoiceExtractor
    if name == "AnomalyDetector":
        from .logistics.anomaly_detector import AnomalyDetector
        return AnomalyDetector
    if name == "POMatcher":
        from .logistics.po_matcher import POMatcher
        return POMatcher
    
    # Medical domain
    if name == "MedicalRAGChain":
        from .medical.medical_rag import MedicalRAGChain
        return MedicalRAGChain
    if name == "DrugInteractionChecker":
        from .medical.drug_checker import DrugInteractionChecker
        return DrugInteractionChecker
    if name == "ICD10Extractor":
        from .medical.icd10_extractor import ICD10Extractor
        return ICD10Extractor
    if name == "PIIRedactor":
        from .medical.pii_redactor import PIIRedactor
        return PIIRedactor
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# DVMELTSS-L: Module initialization logging
def _log_module_init() -> None:
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Domains module loaded | version={__version__} | {__description__}")

# Auto-log on import (safe — only runs once per process)
_log_module_init()