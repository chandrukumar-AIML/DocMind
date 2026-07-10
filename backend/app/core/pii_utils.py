# HIPAA-FIX: Redact before external calls
"""
Domain-aware PII scrubbing utilities for evaluation and logging.

Supports:
- Medical (HIPAA): SSN, MRN, DOB, patient names
- Legal: Contract parties, signatures, confidential terms
- Logistics: Account numbers, PO references, vendor details
- General: Email, phone, credit cards, IBAN

Usage:
    from app.core.pii_utils import scrub_pii_for_evaluation
    safe_text = scrub_pii_for_evaluation(text, domain="medical")
"""

from __future__ import annotations

import re
from typing import Final, Literal

# DVMELTSS-S: Immutable PII patterns — compiled once, reused everywhere
_PII_EMAIL: Final = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,}\b")
_PII_PHONE: Final = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_PII_SSN: Final = re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")
_PII_CARD: Final = re.compile(r"\b(?:\d{4}[\s\-]?){3}\d{4}\b")
_PII_IBAN: Final = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")

# Medical/HIPAA specific
_PII_MRN: Final = re.compile(
    r"\b(?:MRN|Medical\s+Record(?:\s+Number)?|Patient\s+ID)[:\s]*[A-Z0-9\-]{5,20}\b",
    re.I,
)
_PII_NPI: Final = re.compile(r"\b(?:NPI)[:\s]*\d{10}\b", re.I)
_PII_DOB: Final = re.compile(r"\b(?:DOB|Date\s+of\s+Birth|born)[:\s]*\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}\b", re.I)
_PII_PATIENT_NAME: Final = re.compile(r"\b(?:Patient|Name|Pt)[:\s]*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", re.I)

# Legal specific
_PII_CONTRACT_PARTY: Final = re.compile(r"\b(?:Party\s+[A-Z]|Signatory|Contractor)[:\s]*([A-Z][a-z\s]+)", re.I)
_PII_SIGNATURE: Final = re.compile(r"\b(?:Signed|Signature)[:\s]*([A-Z][a-z\s]+)", re.I)

# Logistics specific
_PII_ACCOUNT: Final = re.compile(r"\b(?:Account|Acct)?\s*#?[:\s]*\d{8,17}\b", re.I)
_PII_PO: Final = re.compile(r"\b(?:PO|Purchase\s+Order|P\.?O\.?)\s*#?[:\s]*[A-Z0-9\-]{5,20}\b", re.I)
_PII_VENDOR: Final = re.compile(r"\b(?:Vendor|Supplier|Provider)[:\s]*([A-Z][a-z\s]+)", re.I)

# General identifiers
_PII_PASSPORT: Final = re.compile(r"\b[A-Z]{1,2}\d{6,9}[A-Z]?\b")
_PII_DRIVERS_LICENSE: Final = re.compile(r"\b(?:DL|Driver'?s?\s+License)[:\s]*[A-Z0-9]{7,15}\b", re.I)

# Replacement mappings
_PII_REPLACEMENTS: Final = {
    "EMAIL": "[EMAIL REDACTED]",
    "PHONE": "[PHONE REDACTED]",
    "SSN": "[SSN REDACTED]",
    "CARD": "[CARD REDACTED]",
    "IBAN": "[IBAN REDACTED]",
    "MRN": "[MRN REDACTED]",
    "NPI": "[NPI REDACTED]",
    "DOB": "[DOB REDACTED]",
    "PATIENT_NAME": "[PATIENT NAME REDACTED]",
    "CONTRACT_PARTY": "[PARTY REDACTED]",
    "SIGNATURE": "[SIGNATURE REDACTED]",
    "ACCOUNT": "[ACCOUNT REDACTED]",
    "PO": "[PO REDACTED]",
    "VENDOR": "[VENDOR REDACTED]",
    "PASSPORT": "[PASSPORT REDACTED]",
    "LICENSE": "[LICENSE REDACTED]",
}

DomainType = Literal["medical", "legal", "logistics", "general", "all"]


def scrub_pii_for_evaluation(
    text: str,
    domain: DomainType = "all",
    preserve_structure: bool = True,
) -> str:
    """
    Scrub PII from text for safe evaluation/logging.

    Args:
        text: Raw text to sanitize
        domain: Domain-specific PII patterns to apply
        preserve_structure: Keep text structure (newlines, spacing) after redaction

    Returns:
        Sanitized text with PII replaced by placeholders
    """
    if not text or not isinstance(text, str):
        return ""

    result = text

    # Always apply general patterns
    general_patterns = [
        (_PII_EMAIL, _PII_REPLACEMENTS["EMAIL"]),
        (_PII_PHONE, _PII_REPLACEMENTS["PHONE"]),
        (_PII_SSN, _PII_REPLACEMENTS["SSN"]),
        (_PII_CARD, _PII_REPLACEMENTS["CARD"]),
        (_PII_IBAN, _PII_REPLACEMENTS["IBAN"]),
        (_PII_PASSPORT, _PII_REPLACEMENTS["PASSPORT"]),
        (_PII_DRIVERS_LICENSE, _PII_REPLACEMENTS["LICENSE"]),
    ]

    for pattern, replacement in general_patterns:
        result = pattern.sub(replacement, result)

    # Apply domain-specific patterns
    if domain in ("medical", "all"):
        medical_patterns = [
            (_PII_MRN, _PII_REPLACEMENTS["MRN"]),
            (_PII_NPI, _PII_REPLACEMENTS["NPI"]),
            (_PII_DOB, _PII_REPLACEMENTS["DOB"]),
            (_PII_PATIENT_NAME, _PII_REPLACEMENTS["PATIENT_NAME"]),
        ]
        for pattern, replacement in medical_patterns:
            result = pattern.sub(replacement, result)

    if domain in ("legal", "all"):
        legal_patterns = [
            (_PII_CONTRACT_PARTY, _PII_REPLACEMENTS["CONTRACT_PARTY"]),
            (_PII_SIGNATURE, _PII_REPLACEMENTS["SIGNATURE"]),
        ]
        for pattern, replacement in legal_patterns:
            result = pattern.sub(replacement, result)

    if domain in ("logistics", "all"):
        logistics_patterns = [
            (_PII_ACCOUNT, _PII_REPLACEMENTS["ACCOUNT"]),
            (_PII_PO, _PII_REPLACEMENTS["PO"]),
            (_PII_VENDOR, _PII_REPLACEMENTS["VENDOR"]),
        ]
        for pattern, replacement in logistics_patterns:
            result = pattern.sub(replacement, result)

    # Preserve structure if requested
    if preserve_structure:
        # Collapse multiple spaces but keep newlines
        result = re.sub(r"[ \t]+", " ", result)

    return result.strip()


def is_pii_present(text: str, domain: DomainType = "all") -> bool:
    """
    Check if text contains any PII patterns.

    Args:
        text: Text to check
        domain: Domain-specific patterns to check

    Returns:
        True if any PII pattern matches
    """
    if not text:
        return False

    # Check general patterns
    if any(
        p.search(text)
        for p, _ in [
            (_PII_EMAIL, ""),
            (_PII_PHONE, ""),
            (_PII_SSN, ""),
            (_PII_CARD, ""),
            (_PII_IBAN, ""),
            (_PII_PASSPORT, ""),
        ]
    ):
        return True

    # Check domain-specific
    if domain in ("medical", "all"):
        if any(p.search(text) for p in [_PII_MRN, _PII_NPI, _PII_DOB, _PII_PATIENT_NAME]):
            return True

    if domain in ("legal", "all"):
        if any(p.search(text) for p in [_PII_CONTRACT_PARTY, _PII_SIGNATURE]):
            return True

    if domain in ("logistics", "all"):
        if any(p.search(text) for p in [_PII_ACCOUNT, _PII_PO, _PII_VENDOR]):
            return True

    return False


# DVMELTSS-M: Explicit module exports
__all__ = ["scrub_pii_for_evaluation", "is_pii_present"]
# Local smoke test entry point. Run: python -m

