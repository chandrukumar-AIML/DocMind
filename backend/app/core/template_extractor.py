# backend/app/core/template_extractor.py
"""No-code extraction template engine: LLM-driven structured field extraction."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_LLM_TIMEOUT = 60.0

# ── Pre-built templates ───────────────────────────────────────

BUILTIN_TEMPLATES: dict[str, dict] = {
    "invoice": {
        "name": "Invoice",
        "fields": [
            {
                "name": "invoice_number",
                "type": "string",
                "description": "Invoice/bill number",
                "required": True,
            },
            {
                "name": "invoice_date",
                "type": "date",
                "description": "Date of invoice",
                "required": True,
            },
            {
                "name": "vendor_name",
                "type": "string",
                "description": "Vendor/supplier name",
                "required": True,
            },
            {
                "name": "total_amount",
                "type": "number",
                "description": "Total payable amount",
                "required": True,
            },
            {
                "name": "gst_number",
                "type": "string",
                "description": "GST/GSTIN of vendor",
                "required": False,
            },
            {
                "name": "line_items",
                "type": "list",
                "description": "List of items with description, qty, rate",
                "required": False,
            },
        ],
    },
    "contract": {
        "name": "Contract",
        "fields": [
            {
                "name": "parties",
                "type": "list",
                "description": "Contracting parties (names)",
                "required": True,
            },
            {
                "name": "effective_date",
                "type": "date",
                "description": "Contract effective/start date",
                "required": True,
            },
            {
                "name": "expiry_date",
                "type": "date",
                "description": "Contract expiry/end date",
                "required": False,
            },
            {
                "name": "governing_law",
                "type": "string",
                "description": "Governing law/jurisdiction",
                "required": False,
            },
            {
                "name": "total_value",
                "type": "number",
                "description": "Total contract value",
                "required": False,
            },
            {
                "name": "penalty_clause",
                "type": "string",
                "description": "Penalty/breach clause text",
                "required": False,
            },
        ],
    },
    "medical": {
        "name": "Medical Report",
        "fields": [
            {
                "name": "patient_name",
                "type": "string",
                "description": "Patient full name",
                "required": True,
            },
            {
                "name": "date_of_birth",
                "type": "date",
                "description": "Patient date of birth",
                "required": False,
            },
            {
                "name": "diagnosis",
                "type": "string",
                "description": "Primary diagnosis",
                "required": True,
            },
            {
                "name": "medications",
                "type": "list",
                "description": "Prescribed medications",
                "required": False,
            },
            {
                "name": "doctor_name",
                "type": "string",
                "description": "Attending physician",
                "required": False,
            },
            {
                "name": "report_date",
                "type": "date",
                "description": "Report date",
                "required": True,
            },
        ],
    },
    "purchase_order": {
        "name": "Purchase Order",
        "fields": [
            {
                "name": "po_number",
                "type": "string",
                "description": "PO number",
                "required": True,
            },
            {
                "name": "buyer_name",
                "type": "string",
                "description": "Buyer/company name",
                "required": True,
            },
            {
                "name": "delivery_date",
                "type": "date",
                "description": "Expected delivery date",
                "required": False,
            },
            {
                "name": "items",
                "type": "list",
                "description": "Ordered items with qty and price",
                "required": True,
            },
            {
                "name": "total_value",
                "type": "number",
                "description": "Total order value",
                "required": True,
            },
        ],
    },
    "resume": {
        "name": "Resume/CV",
        "fields": [
            {
                "name": "full_name",
                "type": "string",
                "description": "Candidate full name",
                "required": True,
            },
            {
                "name": "email",
                "type": "string",
                "description": "Email address",
                "required": False,
            },
            {
                "name": "phone",
                "type": "string",
                "description": "Phone number",
                "required": False,
            },
            {
                "name": "skills",
                "type": "list",
                "description": "Technical/professional skills",
                "required": False,
            },
            {
                "name": "education",
                "type": "list",
                "description": "Education details",
                "required": False,
            },
            {
                "name": "experience_years",
                "type": "number",
                "description": "Total years of experience",
                "required": False,
            },
        ],
    },
    "bank_statement": {
        "name": "Bank Statement",
        "fields": [
            {
                "name": "account_number",
                "type": "string",
                "description": "Account number (masked)",
                "required": True,
            },
            {
                "name": "account_holder",
                "type": "string",
                "description": "Account holder name",
                "required": True,
            },
            {
                "name": "statement_period",
                "type": "string",
                "description": "Statement period (from–to)",
                "required": True,
            },
            {
                "name": "opening_balance",
                "type": "number",
                "description": "Opening balance",
                "required": False,
            },
            {
                "name": "closing_balance",
                "type": "number",
                "description": "Closing balance",
                "required": False,
            },
            {
                "name": "total_credits",
                "type": "number",
                "description": "Total credits in period",
                "required": False,
            },
            {
                "name": "total_debits",
                "type": "number",
                "description": "Total debits in period",
                "required": False,
            },
        ],
    },
}


async def ensure_template_schema() -> None:
    """Create extraction_templates and extraction_results tables."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS extraction_templates (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id VARCHAR(64) NOT NULL,
                name         VARCHAR(128) NOT NULL,
                slug         VARCHAR(64),
                fields       JSONB NOT NULL DEFAULT '[]',
                is_builtin   BOOLEAN NOT NULL DEFAULT FALSE,
                created_by   VARCHAR(64),
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS extraction_results (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id VARCHAR(64) NOT NULL,
                template_id  UUID NOT NULL,
                source_file  TEXT NOT NULL,
                fields       JSONB NOT NULL DEFAULT '{}',
                confidence   JSONB NOT NULL DEFAULT '{}',
                raw_output   TEXT,
                created_at   TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            )
        """)
        )
        for idx in [
            "CREATE INDEX IF NOT EXISTS ix_ext_templates_workspace ON extraction_templates(workspace_id)",
            "CREATE INDEX IF NOT EXISTS ix_ext_results_source ON extraction_results(source_file)",
        ]:
            await conn.execute(text(idx))
    logger.info("Template schema verified")


async def get_template(template_id: str, workspace_id: str) -> Optional[dict]:
    """Fetch a template (custom or workspace-level)."""
    async with async_engine.begin() as conn:
        row = await conn.execute(
            text("""
            SELECT id, name, slug, fields, is_builtin, created_by
            FROM extraction_templates
            WHERE id = :id AND (workspace_id = :ws OR is_builtin = TRUE)
        """),
            {"id": template_id, "ws": workspace_id},
        )
        r = row.fetchone()
    if not r:
        return None
    return {
        "id": str(r[0]),
        "name": r[1],
        "slug": r[2],
        "fields": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
        "is_builtin": r[4],
        "created_by": r[5],
    }


async def run_extraction(
    workspace_id: str,
    template_id: str,
    source_file: str,
) -> dict[str, Any]:
    corr_id = generate_correlation_id("tpl-ext")
    tmpl = await get_template(template_id, workspace_id)
    if not tmpl:
        raise ValueError(f"Template {template_id} not found")

    fields = tmpl["fields"]

    # Fetch document text
    try:
        from app.dependencies import get_store_manager

        store = get_store_manager()
        results = store.similarity_search(
            "extract structured data",
            k=15,
            workspace_id=workspace_id,
            filter={"source_file": source_file},
        )
        doc_text = "\n".join(r.page_content for r in results if hasattr(r, "page_content"))[:6000]
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not fetch doc text for {source_file}: {e}")
        doc_text = f"[Document: {source_file}]"

    # Build extraction prompt
    field_specs = json.dumps(fields, indent=2)
    prompt = (
        f"You are a document data extraction expert. Extract the following fields from the document.\n\n"
        f"Fields to extract:\n{field_specs}\n\n"
        f"Document content:\n{doc_text}\n\n"
        f"Return ONLY a JSON object with two keys:\n"
        f"- 'fields': dict mapping field name to extracted value (null if not found)\n"
        f"- 'confidence': dict mapping field name to confidence score 0.0-1.0\n"
        f"Return ONLY valid JSON."
    )

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
        extracted_fields = parsed.get("fields", {})
        confidence = parsed.get("confidence", {})

    except asyncio.TimeoutError:
        raise ValueError("LLM timeout during extraction")
    except Exception as e:
        logger.error(f"[{corr_id}] LLM extraction failed: {e}")
        extracted_fields = {}
        confidence = {}
        content = str(e)

    # Persist result
    result_id = str(uuid.uuid4())
    try:
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                INSERT INTO extraction_results
                    (id, workspace_id, template_id, source_file, fields, confidence, raw_output)
                VALUES
                    (:id, :ws, :tid, :sf, CAST(:fields AS jsonb), CAST(:conf AS jsonb), :raw)
            """),
                {
                    "id": result_id,
                    "ws": workspace_id,
                    "tid": template_id,
                    "sf": source_file,
                    "fields": json.dumps(extracted_fields, default=str),
                    "conf": json.dumps(confidence, default=str),
                    "raw": content[:5000] if isinstance(content, str) else "",
                },
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Could not persist extraction result: {e}")

    return {
        "result_id": result_id,
        "template_id": template_id,
        "template_name": tmpl["name"],
        "source_file": source_file,
        "fields": extracted_fields,
        "confidence": confidence,
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Template extractor smoke test")
        assert "invoice" in BUILTIN_TEMPLATES
        assert "contract" in BUILTIN_TEMPLATES
        inv = BUILTIN_TEMPLATES["invoice"]
        assert any(f["name"] == "invoice_number" for f in inv["fields"])
        assert any(f["required"] for f in inv["fields"])
        print(f"Built-in templates: {list(BUILTIN_TEMPLATES.keys())}")
        print("Template extractor checks passed")

    asyncio.run(smoke())
