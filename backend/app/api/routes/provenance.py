
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Optional, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query as FastAPIQuery
from pydantic import BaseModel

from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.provenance.store import ProvenanceStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/provenance", tags=["provenance"])

_STORE_TIMEOUT: Final = 30.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class CitationResponse(BaseModel):
    citation_id: str
    answer_id: str
    source_file: str
    page_number: int
    page_display: int
    chunk_text: str
    confidence_score: float
    block_type: str
    highlight_color: str
    char_offset_start: Optional[int] = None
    char_offset_end: Optional[int] = None
    created_at: str


class AnswerResponse(BaseModel):
    answer_id: str
    question: str
    answer_text: str
    workspace_id: str
    thread_id: Optional[str]
    retrieval_mode: Optional[str]
    confidence_score: Optional[float]
    latency_seconds: Optional[float]
    created_at: str
    citations: list[CitationResponse]


def _validate_provenance_inputs(
    answer_id: Optional[str],
    source_file: Optional[str],
    query_text: Optional[str],
    limit: Optional[int],
    offset: Optional[int],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate provenance endpoint inputs before processing."""
    if answer_id is not None and not isinstance(answer_id, str):
        return False, "answer_id must be a string or None"
    if source_file is not None and not isinstance(source_file, str):
        return False, "source_file must be a string or None"
    if query_text is not None and not isinstance(query_text, str):
        return False, "query_text must be a string or None"
    if limit is not None and (not isinstance(limit, int) or limit < 1 or limit > 200):
        return False, "limit must be between 1 and 200"
    if offset is not None and (not isinstance(offset, int) or offset < 0):
        return False, "offset must be >= 0"
    return True, ""


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.get(
    "/answers",
    response_model=list[AnswerResponse],
    summary="List stored answers with citations",
)
async def list_answers(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    source_file: Annotated[Optional[str], FastAPIQuery(max_length=255)] = None,
    limit: Annotated[int, FastAPIQuery(ge=1, le=100)] = 20,
    offset: Annotated[int, FastAPIQuery(ge=0)] = 0,
) -> list[AnswerResponse]:
    corr_id = generate_correlation_id("list_answers")

    # ✅ Validate inputs
    is_valid, error = _validate_provenance_inputs(None, source_file, None, limit, offset, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    store = ProvenanceStore()

    try:
        answers = await asyncio.wait_for(
            store.list_answers(
                workspace_id=user.workspace_id,
                source_file=source_file,
                limit=limit,
                offset=offset,
                correlation_id=corr_id,
            ),
            timeout=_STORE_TIMEOUT,
        )
        return [_dict_to_answer_response(a) for a in (answers or [])]
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] List answers timed out after {_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] List answers failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list answers")


@router.get(
    "/answers/{answer_id}",
    response_model=AnswerResponse,
    summary="Get a specific answer with all its citations",
)
async def get_answer(
    answer_id: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AnswerResponse:
    corr_id = generate_correlation_id("get_answer")

    # ✅ Validate inputs
    is_valid, error = _validate_provenance_inputs(answer_id, None, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    store = ProvenanceStore()

    try:
        answer = await asyncio.wait_for(
            store.get_answer(
                answer_id=answer_id,
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
            ),
            timeout=_STORE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Get answer timed out after {_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Get answer failed: {e}")
        raise HTTPException(status_code=404, detail=f"Answer not found: {answer_id}")

    if not answer:
        raise HTTPException(status_code=404, detail=f"Answer not found in your workspace: {answer_id}")

    return _dict_to_answer_response(answer)


@router.get(
    "/documents/{source_file}/citations",
    summary="Get all citations for a document",
)
async def get_document_citations(
    source_file: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    page_number: Annotated[Optional[int], FastAPIQuery(ge=0)] = None,
    limit: Annotated[int, FastAPIQuery(ge=1, le=200)] = 50,
) -> dict:
    corr_id = generate_correlation_id("doc_citations")

    # ✅ Validate inputs
    is_valid, error = _validate_provenance_inputs(None, source_file, None, limit, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    store = ProvenanceStore()

    try:
        citations = await asyncio.wait_for(
            store.get_citations_for_document(
                source_file=source_file,
                workspace_id=user.workspace_id,
                page_number=page_number,
                limit=limit,
                correlation_id=corr_id,
            ),
            timeout=_STORE_TIMEOUT,
        )
        return {
            "source_file": source_file,
            "workspace_id": user.workspace_id,
            "count": len(citations) if citations else 0,
            "citations": citations or [],
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Get citations timed out after {_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Get citations failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve citations")


@router.get(
    "/documents/{source_file}/stats",
    summary="Get citation statistics for a document",
)
async def get_document_citation_stats(
    source_file: str,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> dict:
    corr_id = generate_correlation_id("doc_citation_stats")

    # ✅ Validate inputs
    is_valid, error = _validate_provenance_inputs(None, source_file, None, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    store = ProvenanceStore()

    try:
        stats = await asyncio.wait_for(
            store.get_document_citation_stats(
                source_file=source_file,
                workspace_id=user.workspace_id,
                correlation_id=corr_id,
            ),
            timeout=_STORE_TIMEOUT,
        )
        return {
            "source_file": source_file,
            "workspace_id": user.workspace_id,
            "stats": stats or {},
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Get stats timed out after {_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Request timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Get stats failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve stats")


@router.get(
    "/search",
    summary="Search stored citation text",
)
async def search_citations(
    q: Annotated[str, FastAPIQuery(..., min_length=3, max_length=200)],
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    limit: Annotated[int, FastAPIQuery(ge=1, le=50)] = 20,
) -> dict:
    corr_id = generate_correlation_id("provenance_search")

    # ✅ Validate inputs + sanitize query
    is_valid, error = _validate_provenance_inputs(None, None, q, limit, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    safe_q = q.strip()[:200]

    store = ProvenanceStore()

    try:
        citations = await asyncio.wait_for(
            store.search_citations(
                query_text=safe_q,
                workspace_id=user.workspace_id,
                limit=limit,
                correlation_id=corr_id,
            ),
            timeout=_STORE_TIMEOUT,
        )
        return {
            "query": safe_q,
            "workspace_id": user.workspace_id,
            "count": len(citations) if citations else 0,
            "citations": citations or [],
            "correlation_id": corr_id,
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Search timed out after {_STORE_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Search timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Search failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to search citations")


# ========================================================================
# HELPER: Dict -> Pydantic conversion
# ========================================================================
def _dict_to_answer_response(a: dict) -> AnswerResponse:
    """Convert dict to AnswerResponse with safe key access."""
    return AnswerResponse(
        answer_id=a.get("answer_id", ""),
        question=a.get("question", ""),
        answer_text=a.get("answer_text", ""),
        workspace_id=a.get("workspace_id", ""),
        thread_id=a.get("thread_id"),
        retrieval_mode=a.get("retrieval_mode"),
        confidence_score=a.get("confidence_score"),
        latency_seconds=a.get("latency_seconds"),
        created_at=a.get("created_at", ""),
        citations=[
            CitationResponse(
                citation_id=c.get("citation_id", ""),
                answer_id=c.get("answer_id", ""),
                source_file=c.get("source_file", ""),
                page_number=c.get("page_number", 0),
                page_display=c.get("page_display", 0),
                chunk_text=c.get("chunk_text", ""),
                confidence_score=c.get("confidence_score", 0.0),
                block_type=c.get("block_type", ""),
                highlight_color=c.get("highlight_color", ""),
                char_offset_start=c.get("char_offset_start"),
                char_offset_end=c.get("char_offset_end"),
                created_at=c.get("created_at", ""),
            )
            for c in a.get("citations", [])
            if isinstance(c, dict)
        ],
    )


def get_provenance_metadata() -> dict[str, Any]:
    """✅ NEW: Return provenance API metadata for monitoring."""
    return {
        "endpoints": [
            "/provenance/answers",
            "/provenance/answers/{answer_id}",
            "/provenance/documents/{source_file}/citations",
            "/provenance/documents/{source_file}/stats",
            "/provenance/search",
        ],
        "timeout_seconds": _STORE_TIMEOUT,
        "limits": {
            "list_answers_limit_max": 100,
            "citations_limit_max": 200,
            "search_limit_max": 50,
            "search_query_max_length": 200,
        },
        "workspace_scoped": True,
        "pii_safe": True,
    }


__all__ = ["router", "get_provenance_metadata"]
# Local smoke test entry point. Run: python -m

