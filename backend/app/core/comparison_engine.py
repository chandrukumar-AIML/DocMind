# backend/app/core/comparison_engine.py
"""Batch cross-document comparison engine using LangChain/LLM."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from enum import Enum
from typing import Optional

from sqlalchemy import text

from app.core.ids import generate_correlation_id
from app.database.engine import async_engine

logger = logging.getLogger(__name__)

_LLM_TIMEOUT = 120.0
_MAX_DOCS = 50
_MIN_DOCS = 2


class ComparisonMode(str, Enum):
    SIMILARITY = "SIMILARITY"
    DIFFERENCE = "DIFFERENCE"
    PATTERN = "PATTERN"
    SUMMARY = "SUMMARY"


async def ensure_comparison_schema() -> None:
    """Create comparison_jobs table if it doesn't exist."""
    async with async_engine.begin() as conn:
        if conn.dialect.name != "postgresql":
            return
        await conn.execute(
            text("""
            CREATE TABLE IF NOT EXISTS comparison_jobs (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                workspace_id  VARCHAR(64) NOT NULL,
                mode          VARCHAR(32) NOT NULL,
                doc_ids       JSONB NOT NULL,
                status        VARCHAR(32) NOT NULL DEFAULT 'pending',
                result        JSONB,
                error_msg     TEXT,
                created_by    VARCHAR(64),
                created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                completed_at  TIMESTAMP WITH TIME ZONE
            )
        """)
        )
        await conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_comparison_jobs_workspace " "ON comparison_jobs(workspace_id)")
        )
    logger.info("Comparison schema verified")


async def create_comparison_job(
    workspace_id: str,
    mode: ComparisonMode,
    source_files: list[str],
    created_by: str,
) -> str:
    job_id = str(uuid.uuid4())
    async with async_engine.begin() as conn:
        await conn.execute(
            text("""
            INSERT INTO comparison_jobs (id, workspace_id, mode, doc_ids, created_by)
            VALUES (:id, :ws, :mode, CAST(:doc_ids AS jsonb), :by)
        """),
            {
                "id": job_id,
                "ws": workspace_id,
                "mode": mode.value,
                "doc_ids": json.dumps(source_files),
                "by": created_by,
            },
        )
    return job_id


async def get_comparison_job(job_id: str, workspace_id: str) -> Optional[dict]:
    async with async_engine.begin() as conn:
        row = await conn.execute(
            text("""
            SELECT id, workspace_id, mode, doc_ids, status, result, error_msg,
                   created_by, created_at, completed_at
            FROM comparison_jobs
            WHERE id = :id AND workspace_id = :ws
        """),
            {"id": job_id, "ws": workspace_id},
        )
        r = row.fetchone()
    if not r:
        return None
    return {
        "job_id": str(r[0]),
        "workspace_id": r[1],
        "mode": r[2],
        "source_files": r[3] if isinstance(r[3], list) else json.loads(r[3] or "[]"),
        "status": r[4],
        "result": r[5],
        "error_msg": r[6],
        "created_by": r[7],
        "created_at": r[8].isoformat() if r[8] else None,
        "completed_at": r[9].isoformat() if r[9] else None,
    }


async def _fetch_doc_chunks(source_file: str, workspace_id: str) -> str:
    """Retrieve top chunks for a document from the vector store."""
    try:
        from app.dependencies import get_store_manager

        store = get_store_manager()
        results = store.similarity_search(source_file, k=10, workspace_id=workspace_id)
        texts = [r.page_content for r in results if hasattr(r, "page_content")]
        return "\n---\n".join(texts[:10])
    except Exception as e:
        logger.warning(f"Could not fetch chunks for {source_file}: {e}")
        return f"[Document: {source_file}]"


def _build_comparison_prompt(
    mode: ComparisonMode,
    doc_texts: dict[str, str],
) -> str:
    doc_block = "\n\n".join(
        f"### Document {i+1}: {name}\n{text[:3000]}" for i, (name, text) in enumerate(doc_texts.items())
    )
    instructions = {
        ComparisonMode.SIMILARITY: (
            "Identify key similarities between these documents. "
            "Return a JSON object with keys: 'common_themes' (list), "
            "'shared_entities' (list), 'similarity_score' (0-100), 'summary' (string)."
        ),
        ComparisonMode.DIFFERENCE: (
            "Identify key differences between these documents. "
            "Return a JSON object with keys: 'differences' (list of objects with "
            "'aspect', 'doc1_value', 'doc2_value'), 'divergence_score' (0-100), 'summary'."
        ),
        ComparisonMode.PATTERN: (
            "Find recurring patterns, clauses, or structures across all documents. "
            "Return JSON with keys: 'patterns' (list), 'frequency' (dict), 'summary'."
        ),
        ComparisonMode.SUMMARY: (
            "Produce a comparative executive summary across all documents. "
            "Return JSON with keys: 'per_doc_summary' (dict), 'cross_doc_insights' (list), "
            "'recommendation' (string)."
        ),
    }
    return (
        f"You are a document comparison expert. {instructions[mode]}\n\n"
        f"Documents to compare:\n\n{doc_block}\n\n"
        "Return ONLY valid JSON."
    )


async def run_comparison(job_id: str, workspace_id: str) -> None:
    """Execute comparison job — run as Celery task or asyncio.create_task()."""
    corr_id = generate_correlation_id("cmp")

    async def _update_status(status: str, result=None, error=None):
        async with async_engine.begin() as conn:
            await conn.execute(
                text("""
                UPDATE comparison_jobs
                SET status = :status,
                    result = CAST(:result AS jsonb),
                    error_msg = :error,
                    completed_at = CASE WHEN :status IN ('done','failed')
                                        THEN NOW() ELSE NULL END
                WHERE id = :id
            """),
                {
                    "id": job_id,
                    "status": status,
                    "result": json.dumps(result) if result else None,
                    "error": error,
                },
            )

    await _update_status("running")

    try:
        # Load job details
        job = await get_comparison_job(job_id, workspace_id)
        if not job:
            await _update_status("failed", error="Job not found")
            return

        mode = ComparisonMode(job["mode"])
        source_files = job["source_files"]

        # Fetch document content
        doc_texts: dict[str, str] = {}
        for sf in source_files[:_MAX_DOCS]:
            doc_texts[sf] = await _fetch_doc_chunks(sf, workspace_id)

        # Build LLM prompt
        prompt = _build_comparison_prompt(mode, doc_texts)

        # Call LLM
        from app.core.vision_llm import get_vision_llm

        llm = get_vision_llm()

        async def _call_llm():
            return (
                await asyncio.get_running_loop().run_in_executor(  # FIXED: get_event_loop() deprecated in Python 3.10+
                    None, lambda: llm.invoke(prompt)
                )
            )

        raw = await asyncio.wait_for(_call_llm(), timeout=_LLM_TIMEOUT)
        content = raw.content if hasattr(raw, "content") else str(raw)

        # Parse JSON from LLM output
        try:
            # Strip markdown code fences if present
            clean = content.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            result = json.loads(clean.strip())
        except Exception:
            result = {"raw_output": content, "parse_error": True}

        result["mode"] = mode.value
        result["doc_count"] = len(source_files)
        result["correlation_id"] = corr_id

        await _update_status("done", result=result)
        logger.info(f"[{corr_id}] Comparison job {job_id} completed")

    except asyncio.TimeoutError:
        await _update_status("failed", error="LLM timeout")
    except Exception as e:
        logger.error(f"[{corr_id}] Comparison job {job_id} failed: {e}", exc_info=True)
        await _update_status("failed", error=str(e)[:300])


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Comparison engine smoke test")
        prompt = _build_comparison_prompt(
            ComparisonMode.SIMILARITY,
            {"doc1.pdf": "Contract for services.", "doc2.pdf": "Service agreement."},
        )
        assert "ONLY valid JSON" in prompt
        assert "Document 1" in prompt
        print("Prompt build OK")
        print("Comparison engine checks passed")

    asyncio.run(smoke())
