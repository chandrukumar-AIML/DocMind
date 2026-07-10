
"""
Legal Domain Module: Contract analysis, risk scoring, and legal QA.

Public API:
    from app.domains.legal import LegalRAGChain, ClauseExtractor, RiskScorer
"""

from __future__ import annotations

# DVMELTSS-M: Explicit public API surface
__all__ = [
    "LegalRAGChain",
    "ClauseExtractor",
    "ObligationParser",
    "RiskScorer",
]

# ASCALE-S: Module metadata
__version__ = "1.0.0"
__domain__ = "legal"


def __getattr__(name: str):
    """Lazy imports to prevent circular dependencies."""
    if name == "LegalRAGChain":
        from .legal_rag import LegalRAGChain

        return LegalRAGChain
    if name == "ClauseExtractor":
        from .clause_extractor import ClauseExtractor

        return ClauseExtractor
    if name == "ObligationParser":
        from .obligation_parser import ObligationParser

        return ObligationParser
    if name == "RiskScorer":
        from .risk_scorer import RiskScorer

        return RiskScorer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _log_module_init() -> None:
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Legal domain module loaded | version={__version__}")


_log_module_init()
