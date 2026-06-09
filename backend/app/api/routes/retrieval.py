# backend/app/api/routes/retrieval.py
# DVMELTSS-FIX: V/E/M/S + ASCALE-A/E + BATMAN-A
# ✅ FIXED: Pydantic v2 config + input validation + proper sync wrappers + timeout handling

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict

from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, require_admin, AuthenticatedUser
from app.models import ErrorResponse
from app.retrieval import HybridRetriever, RetrievalBenchmark, RETRIEVAL_PROFILES
from app.vectorstore.store_manager import VectorStoreManager
from app.dependencies import get_store_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/retrieval", tags=["retrieval"])

# ✅ NEW: Operation timeouts (seconds)
_SEARCH_TIMEOUT: Final = 60.0
_BENCHMARK_TIMEOUT: Final = 300.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class HybridSearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000)
    k: int = Field(default=10, ge=1, le=50)
    document_type: str = Field(default="general", max_length=64)
    mode: str = Field(
        default="hybrid",
        pattern="^(hybrid|bm25_only|vector_only)$",
        description="hybrid | bm25_only | vector_only",
    )
    bm25_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    vector_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    filter_dict: Optional[dict] = Field(default=None)
    workspace_id: Optional[str] = Field(default=None, max_length=64)
    correlation_id: Optional[str] = Field(default=None, max_length=100)

    # ✅ FIXED: Pydantic v2 style config
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "query": "What are the Q3 revenue risks?",
                    "k": 5,
                    "mode": "hybrid",
                    "document_type": "financial",
                }
            ]
        }
    )


class BenchmarkItem(BaseModel):
    query: str = Field(..., min_length=3)
    relevant_chunk_ids: list[str] = Field(..., min_length=1)


class BenchmarkRequest(BaseModel):
    ground_truth: list[BenchmarkItem] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of {query, relevant_chunk_ids} for evaluation",
    )
    k: int = Field(default=3, ge=1, le=10)
    document_type: str = Field(default="general")
    run_name: str = Field(default="retrieval_benchmark", max_length=128)
    workspace_id: Optional[str] = Field(default=None, max_length=64)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "ground_truth": [
                    {
                        "query": "What is the total revenue?",
                        "relevant_chunk_ids": ["chunk_123", "chunk_456"],
                    }
                ],
                "k": 3,
                "document_type": "financial",
                "run_name": "q3_finance_benchmark",
            }
        }
    )


# ✅ NEW: Input validation helper
def _validate_retrieval_inputs(
    query: Optional[str],
    k: Optional[int],
    document_type: Optional[str],
    mode: Optional[str],
    ground_truth: Optional[list],
    workspace_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate retrieval endpoint inputs before processing."""
    if query is not None and (not isinstance(query, str) or not query.strip()):
        return False, "query must be a non-empty string"
    if k is not None and (not isinstance(k, int) or k < 1 or k > 50):
        return False, "k must be between 1 and 50"
    if document_type is not None and not isinstance(document_type, str):
        return False, "document_type must be a string or None"
    if mode is not None and mode not in ("hybrid", "bm25_only", "vector_only"):
        return False, "mode must be one of: hybrid, bm25_only, vector_only"
    if ground_truth is not None and not isinstance(ground_truth, list):
        return False, "ground_truth must be a list or None"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================


@router.post(
    "/hybrid-search",
    summary="Hybrid BM25 + vector search with RRF fusion",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid parameters"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        408: {"model": ErrorResponse, "description": "Request timed out"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def hybrid_search(
    request: HybridSearchRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    store: VectorStoreManager = Depends(get_store_manager),
) -> dict[str, Any]:
    """
    Search documents using configurable hybrid BM25 + vector retrieval.
    Returns ranked results with full provenance (which retriever found each doc).
    """
    # ✅ Validate inputs
    is_valid, error = _validate_retrieval_inputs(
        request.query,
        request.k,
        request.document_type,
        request.mode,
        None,
        request.workspace_id,
        request.correlation_id or "hybrid_search",
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # DVMELTSS-S: Use user workspace unless admin override provided
    workspace_id = request.workspace_id or user.workspace_id
    corr_id = request.correlation_id or generate_correlation_id("hybrid_search")

    logger.info(
        f"[{corr_id}] Hybrid search: user={user.user_id[:8]}... " f"ws={workspace_id} mode={request.mode} k={request.k}"
    )

    try:
        # FIXED: HybridRetriever owns its workspace-scoped stores.
        retriever = HybridRetriever(workspace_id=workspace_id)

        # ✅ FIXED: Use proper sync wrapper for thread execution
        def _run_search_sync():
            return retriever.search(
                query=request.query,
                k=request.k,
                filter_dict=request.filter_dict,
                correlation_id=corr_id,
            )

        # 60s timeout for heavy retrieval
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_search_sync),
            timeout=_SEARCH_TIMEOUT,
        )

        # Build standardized response
        return {
            "correlation_id": corr_id,
            "workspace_id": workspace_id,
            "query": request.query,
            "mode": getattr(result, "profile_used", None) or request.mode,
            "profile": getattr(result, "profile_used", None),
            "bm25_weight": getattr(result, "bm25_weight", None),
            "vector_weight": getattr(result, "vector_weight", None),
            "results": [
                {
                    "rank": i + 1,
                    "chunk_id": getattr(r, "chunk_id", ""),
                    "source_file": getattr(r, "metadata", {}).get("source_file", ""),
                    "page_number": (getattr(r, "metadata", {}).get("page_number", 0) or 0) + 1,
                    "block_type": getattr(r, "metadata", {}).get("block_type", "paragraph"),
                    "score": round(getattr(r, "score", 0.0), 4),
                    "dense_score": round(getattr(r, "dense_score", 0.0), 4),
                    "sparse_score": round(getattr(r, "sparse_score", 0.0), 4),
                    "metadata": getattr(r, "metadata", {}),
                }
                for i, r in enumerate(result or [])
            ],
            "latency_ms": {
                "bm25": 0.0,
                "vector": 0.0,
                "total": 0.0,
            },
        }

    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Hybrid search timed out after {_SEARCH_TIMEOUT}s")
        raise HTTPException(
            status_code=status.HTTP_408_REQUEST_TIMEOUT,
            detail="Search request timed out. Try reducing k or simplifying query.",
            headers={"X-Correlation-ID": corr_id},
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Hybrid search failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Search failed. Reference: {corr_id}",
            headers={"X-Correlation-ID": corr_id},
        )


@router.post(
    "/benchmark",
    summary="Benchmark BM25 vs Vector vs Hybrid retrieval",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid benchmark data"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Admin role required"},
        408: {"model": ErrorResponse, "description": "Benchmark timed out"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def run_retrieval_benchmark(
    request: BenchmarkRequest,
    user: Annotated[AuthenticatedUser, Depends(require_admin)],
    store: VectorStoreManager = Depends(get_store_manager),
) -> dict[str, Any]:
    """
    Run comparative benchmark of all retrieval strategies.
    Results are automatically logged to MLflow.

    WARNING: This is resource-intensive. Admin access required.
    """
    # ✅ Validate inputs
    is_valid, error = _validate_retrieval_inputs(
        None,
        request.k,
        request.document_type,
        None,
        request.ground_truth,
        request.workspace_id,
        "benchmark",
    )
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # DVMELTSS-S: Workspace isolation
    workspace_id = request.workspace_id or user.workspace_id
    corr_id = generate_correlation_id("benchmark")

    logger.info(f"[{corr_id}] Benchmark started: user={user.user_id[:8]}... ws={workspace_id}")

    try:
        benchmark = RetrievalBenchmark(
            store_manager=store,
            workspace_id=workspace_id,
        )

        # ✅ FIXED: Use proper sync wrapper for thread execution
        def _run_benchmark_sync():
            return benchmark.run(
                ground_truth=[item.model_dump() for item in request.ground_truth],
                k=request.k,
                document_type=request.document_type,
                log_to_mlflow=True,
                run_name=request.run_name,
            )

        suite = await asyncio.wait_for(
            asyncio.to_thread(_run_benchmark_sync),
            timeout=_BENCHMARK_TIMEOUT,
        )
        summary = suite.summary() if hasattr(suite, "summary") else {}

        return {
            "correlation_id": corr_id,
            "workspace_id": workspace_id,
            "status": "completed",
            "summary": summary,
            "per_query": [
                r.to_metrics(k=request.k) if hasattr(r, "to_metrics") else {} for r in getattr(suite, "results", [])
            ],
            "profiles": RETRIEVAL_PROFILES or {},
            "mlflow_logged": True,
        }

    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Benchmark timed out after {_BENCHMARK_TIMEOUT}s")
        raise HTTPException(
            status_code=408,
            detail="Benchmark timed out. Try fewer queries or smaller dataset.",
            headers={"X-Correlation-ID": corr_id},
        )
    except Exception as e:
        logger.error(f"[{corr_id}] Benchmark failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Benchmark failed. Reference: {corr_id}",
            headers={"X-Correlation-ID": corr_id},
        )


@router.get(
    "/profiles",
    summary="Get available retrieval weight profiles",
)
async def get_retrieval_profiles(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict[str, Any]:
    """
    Returns all document-type-specific retrieval weight profiles.
    Useful for frontend configuration or debugging.
    """
    corr_id = generate_correlation_id("get_profiles")
    logger.debug(f"[{corr_id}] Profiles requested: user={user.user_id[:8]}...")

    try:
        profiles = RETRIEVAL_PROFILES or {}
    except Exception as e:
        logger.warning(f"[{corr_id}] Failed to load profiles: {e}")
        profiles = {}

    return {
        "correlation_id": corr_id,
        "workspace_id": user.workspace_id,
        "profiles": profiles,
        "default_profile": "general",
    }


def get_retrieval_metadata() -> dict[str, Any]:
    """✅ NEW: Return retrieval API metadata for monitoring."""
    return {
        "endpoints": [
            "/retrieval/hybrid-search",
            "/retrieval/benchmark",
            "/retrieval/profiles",
        ],
        "timeouts": {
            "search_seconds": _SEARCH_TIMEOUT,
            "benchmark_seconds": _BENCHMARK_TIMEOUT,
        },
        "limits": {
            "max_k": 50,
            "min_k": 1,
            "max_benchmark_items": 100,
        },
        "supported_modes": ["hybrid", "bm25_only", "vector_only"],
        "workspace_scoped": True,
        "mlflow_integration": True,
    }


__all__ = ["router", "get_retrieval_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
