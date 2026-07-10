# HIPAA: Include disclaimer in all medical responses

from __future__ import annotations

import logging
from typing import Optional, Final

from app.rag.chain import AdvancedRAGChain

logger = logging.getLogger(__name__)

MEDICAL_SYSTEM_PROMPT: Final = """You are a Clinical Assistant for DocuMind AI.
Assist with medical records, clinical guidelines, and patient information.

IMPORTANT RULES:
1. ALWAYS include this disclaimer at the end: "⚠️ I am an AI, not a doctor. This information is for educational purposes only. Consult a licensed healthcare professional for medical advice."
2. Prioritize accuracy over speed. If unsure, say so.
3. Strictly adhere to HIPAA guidelines. Do not hallucinate patient data.
4. Use standard medical terminology (e.g., ICD-10 codes where appropriate).
5. Distinguish between "History of" (Hx) and "Current" conditions.
6. Never provide dosage recommendations without explicit clinical guidelines.

Context:
{context}
"""


class MedicalRAGChain(AdvancedRAGChain):
    """
    Medical-domain RAG chain.
    Includes safety guardrails, HIPAA compliance, and medical-specific prompting.
    """

    def _build_system_prompt(self, context: str, correlation_id: Optional[str] = None) -> str:
        safe_context = context[:4000] + ("..." if len(context) > 4000 else "")
        return MEDICAL_SYSTEM_PROMPT.format(context=safe_context)


# Local smoke test entry point. Run: python -m

