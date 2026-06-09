# backend/app/domains/medical/__init__.py
# DVMELTSS-FIX: M - Modular, T - Testing, L - Metadata
# OWASP-FIX: 1 - HIPAA compliance

"""
Medical Domain Module: Clinical NLP, coding, and HIPAA compliance.

Public API:
    from app.domains.medical import MedicalRAGChain, DrugInteractionChecker, PIIRedactor

⚠️ HIPAA NOTICE: All medical data must be redacted BEFORE external API calls.
"""

from __future__ import annotations

__all__ = [
    "MedicalRAGChain",
    "DrugInteractionChecker",
    "ICD10Extractor",
    "PIIRedactor",
]

__version__ = "1.0.0"
__domain__ = "medical"
__compliance__ = "HIPAA-ready (redact before external calls)"


def __getattr__(name: str):
    """Lazy imports to prevent circular dependencies."""
    if name == "MedicalRAGChain":
        from .medical_rag import MedicalRAGChain

        return MedicalRAGChain
    if name == "DrugInteractionChecker":
        from .drug_checker import DrugInteractionChecker

        return DrugInteractionChecker
    if name == "ICD10Extractor":
        from .icd10_extractor import ICD10Extractor

        return ICD10Extractor
    if name == "PIIRedactor":
        from .pii_redactor import PIIRedactor

        return PIIRedactor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _log_module_init() -> None:
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Medical domain module loaded | version={__version__} | {__compliance__}")


_log_module_init()
