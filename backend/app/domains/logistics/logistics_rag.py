
from __future__ import annotations

import logging
from typing import Optional, Final

from app.rag.chain import AdvancedRAGChain

logger = logging.getLogger(__name__)

LOGISTICS_SYSTEM_PROMPT: Final = """You are a Logistics and Supply Chain Expert for DocuMind AI.
Answer questions about invoices, packing slips, bills of lading, and POs.

IMPORTANT RULES:
1. Be precise with numbers (amounts, weights, quantities).
2. Distinguish between 'Gross Weight' and 'Net Weight'.
3. Identify Incoterms (e.g., FOB, CIF) and their implications on risk/cost.
4. Highlight discrepancies between POs and Invoices if detected in context.
5. If tracking information is requested and not found, suggest checking the carrier portal.

Context:
{context}
"""


class LogisticsRAGChain(AdvancedRAGChain):
    """
    Logistics-domain RAG chain.
    Uses context-aware prompting for supply chain documents.
    """

    def _build_system_prompt(self, context: str, correlation_id: Optional[str] = None) -> str:
        safe_context = context[:4000] + ("..." if len(context) > 4000 else "")
        return LOGISTICS_SYSTEM_PROMPT.format(context=safe_context)


# Local smoke test entry point. Run: python -m

