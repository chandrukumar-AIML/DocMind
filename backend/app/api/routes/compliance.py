# backend/app/api/routes/compliance.py
"""Regulatory compliance checking API."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.core.ids import generate_correlation_id
from app.core.compliance_checker import check_compliance, SUPPORTED_REGULATIONS
from app.database.engine import async_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/compliance", tags=["compliance"])


class ComplianceCheckRequest(BaseModel):
    source_file: str = Field(..., min_length=1, max_length=1024)
    regulations: list[str] = Field(..., min_length=1)


@router.get("/regulations")
async def list_supported_regulations() -> dict[str, Any]:
    return {
        "regulations": {code: info["name"] for code, info in SUPPORTED_REGULATIONS.items()},
        "total": len(SUPPORTED_REGULATIONS),
    }


@router.post("/check")
async def run_compliance_check(
    req: ComplianceCheckRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("comp-check")

    invalid = set(req.regulations) - set(SUPPORTED_REGULATIONS.keys())
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported regulations: {invalid}. Use GET /compliance/regulations for valid codes.",
        )

    try:
        result = await check_compliance(
            workspace_id=user.workspace_id,
            source_file=req.source_file,
            regulations=req.regulations,
            created_by=user.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"[{corr_id}] Compliance check failed: {e}")
        raise HTTPException(status_code=500, detail="Compliance check failed")

    return result


@router.get("/history/{source_file:path}")
async def get_compliance_history(
    source_file: str,
    limit: int = 20,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("comp-hist")
    try:
        async with async_engine.begin() as conn:
            rows = await conn.execute(
                text("""
                SELECT id, regulations, scores, overall_score, violation_count_calc,
                       created_at
                FROM (
                    SELECT id, regulations, scores, overall_score,
                           jsonb_array_length(violations) as violation_count_calc,
                           created_at
                    FROM compliance_results
                    WHERE workspace_id = :ws AND source_file = :sf
                    ORDER BY created_at DESC
                    LIMIT :lim
                ) t
            """),
                {"ws": user.workspace_id, "sf": source_file, "lim": min(limit, 100)},
            )
            results = rows.fetchall()
    except Exception:
        # Fallback without subquery if jsonb_array_length not available
        try:
            async with async_engine.begin() as conn:
                rows = await conn.execute(
                    text("""
                    SELECT id, regulations, scores, overall_score, NULL, created_at
                    FROM compliance_results
                    WHERE workspace_id = :ws AND source_file = :sf
                    ORDER BY created_at DESC
                    LIMIT :lim
                """),
                    {
                        "ws": user.workspace_id,
                        "sf": source_file,
                        "lim": min(limit, 100),
                    },
                )
                results = rows.fetchall()
        except Exception as e2:
            raise HTTPException(status_code=500, detail=f"Failed to fetch history: {e2}")

    return {
        "source_file": source_file,
        "history": [
            {
                "result_id": str(r[0]),
                "regulations": r[1] if isinstance(r[1], list) else json.loads(r[1] or "[]"),
                "scores": r[2] if isinstance(r[2], dict) else {},
                "overall_score": r[3],
                "violation_count": r[4] or 0,
                "created_at": r[5].isoformat() if r[5] else None,
            }
            for r in results
        ],
        "correlation_id": corr_id,
    }


@router.get("/result/{result_id}")
async def get_compliance_result(
    result_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, Any]:
    corr_id = generate_correlation_id("comp-result")
    async with async_engine.begin() as conn:
        row = await conn.execute(
            text("""
            SELECT id, source_file, regulations, scores, violations,
                   recommendations, overall_score, created_at
            FROM compliance_results
            WHERE id = :id AND workspace_id = :ws
        """),
            {"id": result_id, "ws": user.workspace_id},
        )
        r = row.fetchone()

    if not r:
        raise HTTPException(status_code=404, detail="Compliance result not found")

    return {
        "result_id": str(r[0]),
        "source_file": r[1],
        "regulations_checked": r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]"),
        "scores": r[3] if isinstance(r[3], dict) else {},
        "violations": r[4] if isinstance(r[4], list) else [],
        "recommendations": r[5] if isinstance(r[5], list) else [],
        "overall_score": r[6],
        "created_at": r[7].isoformat() if r[7] else None,
        "correlation_id": corr_id,
    }


if __name__ == "__main__":
    import asyncio

    async def smoke():
        print("Compliance routes smoke test")
        req = ComplianceCheckRequest(
            source_file="contracts/agreement.pdf",
            regulations=["GDPR", "INDIAN_CONTRACT"],
        )
        assert "GDPR" in req.regulations
        invalid = set(req.regulations) - set(SUPPORTED_REGULATIONS.keys())
        assert not invalid, f"Unexpected invalid: {invalid}"
        print("ComplianceCheckRequest validation OK")
        print("Compliance routes checks passed")

    asyncio.run(smoke())
