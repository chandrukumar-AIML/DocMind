# backend/app/api/routes/comparison.py
"""Batch cross-document comparison API routes."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.comparison_engine import (
    ComparisonMode,
    create_comparison_job,
    get_comparison_job,
    run_comparison,
    _MAX_DOCS,
    _MIN_DOCS,
)
from app.core.ids import generate_correlation_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/comparison", tags=["comparison"])


class ComparisonRequest(BaseModel):
    source_files: list[str] = Field(..., min_length=_MIN_DOCS)
    mode: ComparisonMode = ComparisonMode.SIMILARITY

    @classmethod
    def __get_validators__(cls):
        yield cls._validate

    @classmethod
    def _validate(cls, v):
        return v

    def model_post_init(self, __context: Any) -> None:
        if len(self.source_files) > _MAX_DOCS:
            raise ValueError(f"Maximum {_MAX_DOCS} documents per comparison")


class ComparisonJobResponse(BaseModel):
    job_id: str
    status: str
    mode: str
    source_files: list[str]
    correlation_id: str


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def start_comparison(
    req: ComparisonRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ComparisonJobResponse:
    corr_id = generate_correlation_id("cmp-start")

    if len(req.source_files) < _MIN_DOCS:
        raise HTTPException(status_code=422, detail=f"At least {_MIN_DOCS} documents required")
    if len(req.source_files) > _MAX_DOCS:
        raise HTTPException(status_code=422, detail=f"Maximum {_MAX_DOCS} documents allowed")

    try:
        job_id = await create_comparison_job(
            workspace_id=user.workspace_id,
            mode=req.mode,
            source_files=req.source_files,
            created_by=user.user_id,
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Failed to create comparison job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create comparison job")

    # Run async (fire-and-forget background task)
    asyncio.create_task(run_comparison(job_id, user.workspace_id))
    logger.info(f"[{corr_id}] Comparison job {job_id} started ({req.mode}, {len(req.source_files)} docs)")

    return ComparisonJobResponse(
        job_id=job_id,
        status="pending",
        mode=req.mode.value,
        source_files=req.source_files,
        correlation_id=corr_id,
    )


@router.get("/status/{job_id}")
async def get_comparison_status(
    job_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("cmp-status")
    job = await get_comparison_job(job_id, user.workspace_id)
    if not job:
        raise HTTPException(status_code=404, detail="Comparison job not found")
    job["correlation_id"] = corr_id
    return job


@router.get("/list")
async def list_comparison_jobs(
    limit: int = 20,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    from app.database.engine import async_engine
    from sqlalchemy import text
    import json

    corr_id = generate_correlation_id("cmp-list")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text("""
                SELECT id, mode, doc_ids, status, created_at, completed_at
                FROM comparison_jobs
                WHERE workspace_id = :ws
                ORDER BY created_at DESC
                LIMIT :lim
            """),
                {"ws": user.workspace_id, "lim": min(limit, 100)},
            )
            jobs = rows.fetchall()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list jobs: {e}")

    return {
        "jobs": [
            {
                "job_id": str(j[0]),
                "mode": j[1],
                "doc_count": len(j[2] if isinstance(j[2], list) else json.loads(j[2] or "[]")),
                "status": j[3],
                "created_at": j[4].isoformat() if j[4] else None,
                "completed_at": j[5].isoformat() if j[5] else None,
            }
            for j in jobs
        ],
        "total": len(jobs),
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Comparison routes smoke test")
        req = ComparisonRequest(
            source_files=["a.pdf", "b.pdf"],
            mode=ComparisonMode.DIFFERENCE,
        )
        assert req.mode == ComparisonMode.DIFFERENCE
        print("ComparisonRequest validation OK")
        print("Comparison routes checks passed")

    asyncio.run(smoke())
