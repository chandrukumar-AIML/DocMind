# backend/app/core/compliance_checker.py
"""Regulatory compliance checker: GDPR, HIPAA, RBI, SEBI, Indian Contract Act, GST."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_LLM_TIMEOUT = 90.0

SUPPORTED_REGULATIONS = {
    "GDPR": {
        "name": "General Data Protection Regulation (EU)",
        "key_requirements": [
            "lawful basis for processing",
            "data subject rights (access, erasure, portability)",
            "data minimization",
            "consent mechanisms",
            "data breach notification within 72 hours",
            "privacy by design",
            "DPO appointment if required",
        ],
    },
    "HIPAA": {
        "name": "Health Insurance Portability and Accountability Act (US)",
        "key_requirements": [
            "PHI protection",
            "minimum necessary standard",
            "notice of privacy practices",
            "business associate agreements",
            "access controls and audit logs",
            "encryption of ePHI",
        ],
    },
    "RBI": {
        "name": "Reserve Bank of India Guidelines",
        "key_requirements": [
            "KYC compliance",
            "data localization (sensitive financial data in India)",
            "fraud risk management",
            "cyber security framework",
            "outsourcing risk management",
            "customer grievance redressal",
        ],
    },
    "SEBI": {
        "name": "Securities and Exchange Board of India",
        "key_requirements": [
            "insider trading policy",
            "disclosure requirements",
            "code of conduct",
            "investor grievance mechanism",
            "SEBI circular compliance",
            "record retention",
        ],
    },
    "INDIAN_CONTRACT": {
        "name": "Indian Contract Act, 1872",
        "key_requirements": [
            "free consent of parties",
            "lawful object",
            "competent parties (age, soundness of mind)",
            "consideration clause",
            "not expressly void",
            "performance obligations",
        ],
    },
    "GST": {
        "name": "Goods and Services Tax (India)",
        "key_requirements": [
            "GSTIN on invoices",
            "HSN/SAC codes",
            "correct tax rate application",
            "input tax credit eligibility",
            "invoice format compliance",
            "filing deadlines",
        ],
    },
    "COMPANIES_ACT": {
        "name": "Companies Act, 2013 (India)",
        "key_requirements": [
            "board resolution requirements",
            "annual return filing (MGT-7)",
            "financial statement filing (AOC-4)",
            "director KYC (DIN)",
            "related party transaction disclosure",
            "statutory register maintenance",
            "CSR compliance (where applicable)",
        ],
    },
}


async def ensure_compliance_schema() -> None:
    """Create compliance_results table."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS compliance_results (
                id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id   VARCHAR(64) NOT NULL,
                source_file    TEXT NOT NULL,
                regulations    JSONB NOT NULL DEFAULT '[]',
                scores         JSONB NOT NULL DEFAULT '{}',
                violations     JSONB NOT NULL DEFAULT '[]',
                recommendations JSONB NOT NULL DEFAULT '[]',
                overall_score  FLOAT,
                raw_output     TEXT,
                created_by     VARCHAR(64),
                created_at     TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_compliance_workspace ON compliance_results(workspace_id)")
        )
    logger.info("Compliance schema verified")


def _build_compliance_prompt(
    doc_text: str,
    regulations: list[str],
) -> str:
    reg_details = []
    for reg in regulations:
        info = SUPPORTED_REGULATIONS.get(reg, {})
        reqs = info.get("key_requirements", [])
        reg_details.append(f"**{reg}** ({info.get('name', reg)}):\n" + "\n".join(f"  - {r}" for r in reqs))

    reg_block = "\n\n".join(reg_details)

    return (
        f"You are a regulatory compliance expert. Analyze the document for compliance with "
        f"the following regulations:\n\n{reg_block}\n\n"
        f"Document text:\n{doc_text[:5000]}\n\n"
        f"Return a JSON object with:\n"
        f"- 'scores': dict mapping regulation code to score 0-100\n"
        f"- 'violations': list of objects with 'regulation', 'severity' (critical/high/medium/low), "
        f"  'description', 'clause_reference'\n"
        f"- 'recommendations': list of objects with 'regulation', 'action', 'priority'\n"
        f"- 'overall_score': average compliance score 0-100\n"
        f"Return ONLY valid JSON."
    )


async def check_compliance(
    workspace_id: str,
    source_file: str,
    regulations: list[str],
    created_by: str,
) -> dict[str, Any]:
    corr_id = generate_correlation_id("comp")

    # Validate regulations
    invalid = set(regulations) - set(SUPPORTED_REGULATIONS.keys())
    if invalid:
        raise ValueError(f"Unsupported regulations: {invalid}. Supported: {list(SUPPORTED_REGULATIONS.keys())}")

    # Fetch document text
    try:
        from app.dependencies import get_store_manager

        store = get_store_manager()
        results = store.similarity_search(
            "regulatory compliance requirements",
            k=20,
            workspace_id=workspace_id,
            filter={"source_file": source_file},
        )
        doc_text = "\n".join(r.page_content for r in results if hasattr(r, "page_content"))[:5000]
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not fetch doc text: {e}")
        doc_text = f"[Document: {source_file}]"

    prompt = _build_compliance_prompt(doc_text, regulations)

    try:
        from app.core.vision_llm import get_vision_llm

        llm = get_vision_llm()

        async def _call():
            return (
                await asyncio.get_running_loop().run_in_executor(  # FIXED: get_event_loop() deprecated in Python 3.10+
                    None, lambda: llm.invoke(prompt)
                )
            )

        raw = await asyncio.wait_for(_call(), timeout=_LLM_TIMEOUT)
        content = raw.content if hasattr(raw, "content") else str(raw)
        clean = content.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        parsed = json.loads(clean.strip())

    except asyncio.TimeoutError:
        raise ValueError("LLM timeout during compliance check")
    except Exception as e:
        logger.error(f"[{corr_id}] LLM compliance check failed: {e}")
        parsed = {
            "scores": {r: 0 for r in regulations},
            "violations": [],
            "recommendations": [],
            "overall_score": 0,
        }
        content = str(e)

    scores = parsed.get("scores", {})
    violations = parsed.get("violations", [])
    recommendations = parsed.get("recommendations", [])
    overall_score = parsed.get("overall_score", sum(scores.values()) / max(len(scores), 1))

    # Persist
    result_id = str(uuid.uuid4())
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO compliance_results
                    (id, workspace_id, source_file, regulations, scores,
                     violations, recommendations, overall_score, raw_output, created_by)
                VALUES
                    (:id, :ws, :sf, CAST(:regs AS jsonb), CAST(:scores AS jsonb),
                     CAST(:violations AS jsonb), CAST(:recs AS jsonb), :score, :raw, :by)
            """),
                {
                    "id": result_id,
                    "ws": workspace_id,
                    "sf": source_file,
                    "regs": json.dumps(regulations),
                    "scores": json.dumps(scores, default=str),
                    "violations": json.dumps(violations, default=str),
                    "recs": json.dumps(recommendations, default=str),
                    "score": float(overall_score),
                    "raw": content[:5000] if isinstance(content, str) else "",
                    "by": created_by,
                },
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not persist compliance result: {e}")

    return {
        "result_id": result_id,
        "source_file": source_file,
        "regulations_checked": regulations,
        "scores": scores,
        "violations": violations,
        "recommendations": recommendations,
        "overall_score": overall_score,
        "violation_count": len(violations),
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Compliance checker smoke test")
        assert "GDPR" in SUPPORTED_REGULATIONS
        assert "RBI" in SUPPORTED_REGULATIONS
        assert "GST" in SUPPORTED_REGULATIONS

        prompt = _build_compliance_prompt("Sample contract text", ["GDPR", "INDIAN_CONTRACT"])
        assert "GDPR" in prompt
        assert "INDIAN_CONTRACT" in prompt
        assert "scores" in prompt
        print("Compliance prompt build OK")
        print("Compliance checker checks passed")

    asyncio.run(smoke())
