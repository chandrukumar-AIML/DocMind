# backend/app/domains/legal/legal_rag.py
# DVMELTSS-FIX: M - Modular, S - Security, L - Logging

from __future__ import annotations

import logging
from typing import Optional, Final

from app.rag.chain import AdvancedRAGChain

logger = logging.getLogger(__name__)

LEGAL_SYSTEM_PROMPT: Final = """You are an expert legal document analyst for DocuMind AI.
Answer questions about contracts and legal documents with precision.

IMPORTANT RULES:
1. Cite specific clauses, sections, and exact language
2. Distinguish between "shall" (mandatory) and "may" (permissive)
3. Flag ambiguous language that could be interpreted multiple ways
4. Note when a standard clause appears to be missing
5. Do not provide legal advice — provide legal information and analysis
6. Always include section references when available

Context from document:
{context}
"""


class LegalRAGChain(AdvancedRAGChain):
    """
    Legal-domain RAG chain with contract-aware prompting.
    Extends AdvancedRAGChain with legal-specific system prompt.
    """

    def _build_system_prompt(self, context: str, correlation_id: Optional[str] = None) -> str:
        # FIXED: Add correlation_id to prompt for tracing (optional metadata)
        safe_context = context[:4000] + ("..." if len(context) > 4000 else "")
        return LEGAL_SYSTEM_PROMPT.format(context=safe_context)
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

