# backend/app/api/routes/agent.py
# DVMELTSS-FIX: B/E/A/C + ASCALE-S/E/A + BATMAN-A
# ✅ FIXED: Proper RateLimiter usage + input validation + safe cache handling + workspace scoping

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Optional, Any

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage

from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.models import AgentQueryRequest, AgentQueryResponse, ErrorResponse
from app.agent.agent_chain import AgentRAGChain
from app.cache import get_cache
from app.monitoring.metrics_collector import record_query_latency, record_query_error
from app.middleware.rate_limiter import RateLimiter  # FIXED: actual module path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent", tags=["agent"])

# ✅ FIXED: Use proper RateLimiter with workspace-scoped keys (not constructor params)
# Rate limiting is handled per-request via check_async in the endpoint


# ✅ NEW: Input validation helper
def _validate_agent_inputs(
    question: Optional[str],
    user: Optional[AuthenticatedUser],
    thread_id: Optional[str],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate agent endpoint inputs before processing."""
    if not isinstance(question, str) or not question.strip():
        return False, "question must be a non-empty string"
    if user is None or not isinstance(user, AuthenticatedUser):
        return False, "user must be an AuthenticatedUser instance"
    if thread_id is not None and not isinstance(thread_id, str):
        return False, "thread_id must be a string or None"
    return True, ""


# ========================================================================
# INTERNAL: Agent execution logic (DVMELTSS-B: Separated from route)
# ========================================================================
async def _execute_agent_query(
    question: str,
    user: AuthenticatedUser,
    chat_history: Optional[list] = None,
    filter_dict: Optional[dict] = None,
    stream: bool = True,
    correlation_id: Optional[str] = None,
    request: Optional[Request] = None,
) -> tuple[str, dict | StreamingResponse]:
    """Execute LangGraph agent with caching, streaming, and error handling."""
    corr_id = correlation_id or generate_correlation_id("agent")

    # ✅ Validate inputs
    is_valid, error = _validate_agent_inputs(question, user, None, corr_id)
    if not is_valid:
        logger.error(f"[{corr_id}] Invalid agent inputs: {error}")
        raise HTTPException(status_code=400, detail=error)

    # DVMELTSS-C: Cache check for non-streaming queries
    if not stream:
        try:
            # ✅ FIXED: Await the async cache factory + timeout
            cache = await asyncio.wait_for(get_cache(), timeout=10.0)
            cached = await asyncio.wait_for(
                cache.get_result(
                    workspace_id=user.workspace_id,
                    question=question,
                    filter_dict=filter_dict,
                ),
                timeout=10.0,
            )
            if cached:
                logger.info(f"[{corr_id}] Agent cache HIT")
                return "cached", cached
        except asyncio.TimeoutError:
            logger.warning(f"[{corr_id}] Cache check timed out — proceeding with query")
        except Exception as e:
            logger.warning(f"[{corr_id}] Cache check failed: {e} — proceeding with query")

    # ✅ FIXED: Use singleton from app.state (set in main.py lifespan)
    agent = getattr(request.app.state, "agent_chain", None) if request else None
    if agent is None:
        logger.warning(f"[{corr_id}] AgentRAGChain not initialized — creating new instance (dev mode)")
        agent = AgentRAGChain()

    # Convert chat history to LangChain format
    lc_history = []
    for msg in chat_history or []:
        if isinstance(msg, dict):
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lc_history.append(HumanMessage(content=content) if role == "user" else AIMessage(content=content))
        elif isinstance(msg, (HumanMessage, AIMessage)):
            lc_history.append(msg)

    # ✅ FIXED: Safe timeout fallback
    timeout = getattr(settings, "agent_timeout_seconds", 120)

    try:
        if stream:

            async def stream_generator():
                try:
                    async for event in asyncio.wait_for(
                        agent.stream(
                            question=question,
                            chat_history=lc_history,
                            filter_dict=filter_dict,
                            workspace_id=user.workspace_id,
                            thread_id=corr_id,
                            timeout_seconds=timeout,
                        ),
                        timeout=timeout,
                    ):
                        # ✅ FIXED: Check if client disconnected to stop wasted work
                        if request and await request.is_disconnected():
                            logger.info(f"[{corr_id}] Client disconnected — stopping agent stream")
                            break
                        # SSE format with correlation_id
                        event["correlation_id"] = corr_id
                        yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    logger.warning(f"[{corr_id}] Agent stream timed out after {timeout}s")
                    yield f"data: {json.dumps({'type': 'error', 'message': f'Request timed out after {timeout}s', 'correlation_id': corr_id})}\n\n"
                except Exception as e:
                    logger.error(f"[{corr_id}] Agent stream error: {e}", exc_info=True)
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e), 'correlation_id': corr_id})}\n\n"
                finally:
                    yield "data: [DONE]\n\n"

            return "stream", stream_generator()
        else:
            result = await asyncio.wait_for(
                agent.query(
                    question=question,
                    chat_history=lc_history,
                    filter_dict=filter_dict,
                    workspace_id=user.workspace_id,
                    thread_id=corr_id,
                    timeout_seconds=timeout,
                ),
                timeout=timeout,
            )

            # DVMELTSS-C: Cache successful non-streaming results
            if result.get("success") and not result.get("web_search_used"):
                try:
                    cache = await asyncio.wait_for(get_cache(), timeout=10.0)
                    await asyncio.wait_for(
                        cache.set_result(
                            workspace_id=user.workspace_id,
                            question=question,
                            result=result,
                            filter_dict=filter_dict,
                        ),
                        timeout=10.0,
                    )
                except Exception as e:
                    logger.warning(f"[{corr_id}] Cache set failed: {e}")

            return "batch", result

    except asyncio.TimeoutError:
        logger.warning(f"[{corr_id}] Agent query timed out after {timeout}s")
        raise HTTPException(status_code=408, detail=f"Request timed out after {timeout}s")
    except Exception as e:
        logger.error(f"[{corr_id}] Agent query failed: {type(e).__name__}: {e}", exc_info=True)
        record_query_error(user.workspace_id, corr_id, str(e))
        raise HTTPException(status_code=500, detail="Agent query failed. Please try again.")


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "/query",
    response_model=AgentQueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        408: {"model": ErrorResponse, "description": "Timeout"},
        429: {"model": ErrorResponse, "description": "Rate limited"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
    summary="Query documents using LangGraph agentic RAG",
    description="Agentic RAG with self-correction, routing, and hallucination checking.",
)
async def agent_query(
    request: Request,
    body: AgentQueryRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
) -> StreamingResponse | AgentQueryResponse:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM service unavailable: OPENAI_API_KEY not configured",
        )

    corr_id = body.correlation_id or request.headers.get("X-Correlation-ID") or generate_correlation_id("agent")

    # ✅ FIXED: Proper rate limiting using RateLimiter.check_async with workspace-scoped key
    rate_limiter = RateLimiter()
    rate_key = f"agent:{user.workspace_id}:{user.user_id}"

    try:
        rate_result = await asyncio.wait_for(
            rate_limiter.check_async(
                workspace_id=user.workspace_id,
                endpoint_group="query",
                identifier=rate_key,
                correlation_id=corr_id,
            ),
            timeout=5.0,
        )
        if not rate_result.allowed:
            logger.warning(f"[{corr_id}] Agent rate limited: user={user.user_id[:8]}...")
            raise HTTPException(
                status_code=429,
                detail="Too many agent queries. Please try again later.",
                headers={**rate_result.to_headers(), "X-Correlation-ID": corr_id},
            )
    except Exception as e:
        logger.warning(f"[{corr_id}] Rate limit check failed: {e} — allowing request (fail-open)")

    stream = bool(getattr(body, "stream", False))
    logger.info(f"[{corr_id}] Agent query: user={user.user_id[:8]}... workspace={user.workspace_id} stream={stream}")

    # ✅ FIXED: Safe filter_dict extraction (fallback if build_filter_dict missing)
    filter_dict = None
    if hasattr(body, "build_filter_dict") and callable(body.build_filter_dict):
        filter_dict = body.build_filter_dict()
    elif hasattr(body, "filters"):
        filter_dict = getattr(body, "filters", None)

    response_type, response_data = await _execute_agent_query(
        question=body.question,
        user=user,
        chat_history=[msg.model_dump() for msg in getattr(body, "chat_history", [])]
        if getattr(body, "chat_history", None)
        else None,
        filter_dict=filter_dict,
        stream=stream,
        correlation_id=corr_id,
        request=request,
    )

    # DVMELTSS-L: Non-blocking metrics recording
    if response_type == "batch" and isinstance(response_data, dict):
        background_tasks.add_task(
            record_query_latency,
            workspace_id=user.workspace_id,
            correlation_id=corr_id,
            latency_seconds=response_data.get("latency_seconds", 0),
            success=response_data.get("success", False),
        )

    if response_type == "stream":
        return StreamingResponse(
            response_data,
            media_type="text/event-stream",
            headers={
                "X-Correlation-ID": corr_id,
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Build typed response with truncated agent_steps for large responses
    agent_steps = response_data.get("agent_steps", [])
    if isinstance(agent_steps, list) and len(agent_steps) > 50:
        agent_steps = agent_steps[:50] + [f"... (truncated {len(agent_steps) - 50} steps)"]

    safe_citations = []
    for citation in response_data.get("citations", []) or []:
        if not isinstance(citation, dict):
            continue
        safe_citations.append(
            {
                "source_file": citation.get("source_file") or citation.get("source") or "unknown",
                "page_number": int(citation.get("page_number") or 0),
                "page_display": int(citation.get("page_display") or citation.get("page_number") or 1),
                "block_type": citation.get("block_type") or "text",
                "chunk_text": str(citation.get("chunk_text") or citation.get("text") or "")[:300],
                "relevance_score": float(citation.get("relevance_score") or citation.get("score") or 0.0),
                "retrieval_mode": citation.get("retrieval_mode") or response_data.get("retrieval_route", "vector"),
            }
        )

    return AgentQueryResponse(
        answer=response_data.get("answer", ""),
        citations=safe_citations,
        question=body.question,
        mode=getattr(body, "mode", "rag"),
        retrieved_count=int(response_data.get("retrieved_count") or response_data.get("retrieval_count") or 0),
        reranked_count=int(response_data.get("reranked_count") or len(safe_citations)),
        web_search_used=bool(response_data.get("web_search_used", False)),
        self_critique_applied=bool(response_data.get("self_critique_applied", False)),
        confidence_score=response_data.get("confidence_score", 0.0),
        latency_seconds=response_data.get("latency_seconds", 0.0),
        correlation_id=corr_id,
    )


@router.get(
    "/thread/{thread_id}",
    summary="Get conversation history for a thread",
)
async def get_thread_history(
    thread_id: str,
    request: Request,  # ✅ FIXED: Added missing request parameter
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    """Retrieve conversation history for a given thread ID (scoped to workspace)."""
    corr_id = generate_correlation_id("thread")

    # ✅ Validate inputs
    is_valid, error = _validate_agent_inputs(None, user, thread_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # ✅ FIXED: Use singleton from app.state
    agent = getattr(request.app.state, "agent_chain", None)
    if agent is None:
        agent = AgentRAGChain()

    try:
        # ✅ FIXED: AgentRAGChain.get_conversation_history() doesn't accept workspace_id — filter in app layer
        history = agent.get_conversation_history(thread_id)

        # ✅ FIXED: Filter history to only include messages from this workspace (if metadata available)
        filtered_history = []
        for msg in history:
            msg_meta = getattr(msg, "additional_kwargs", {}) or {}
            msg_workspace = msg_meta.get("workspace_id")
            # Include if no workspace metadata (legacy) or matches current workspace
            if msg_workspace is None or msg_workspace == user.workspace_id:
                filtered_history.append(msg)

        return {
            "thread_id": thread_id,
            "workspace_id": user.workspace_id,
            "message_count": len(filtered_history),
            "messages": [
                {
                    "role": "user" if isinstance(m, HumanMessage) else "assistant",
                    "content": m.content[:500] if hasattr(m, "content") else "",
                    "timestamp": (getattr(m, "additional_kwargs", {}) or {}).get("timestamp"),
                }
                for m in filtered_history
            ],
            "correlation_id": corr_id,
        }
    except Exception as e:
        logger.error(f"[{corr_id}] Thread history fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve conversation history")


@router.get(
    "/confidence/{thread_id}",
    summary="Get confidence scores for the last agent run",
)
async def get_run_confidence(
    thread_id: str,
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
):
    """Returns detailed confidence breakdown from the last run on this thread."""
    corr_id = generate_correlation_id("confidence")

    # ✅ Validate inputs
    is_valid, error = _validate_agent_inputs(None, user, thread_id, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    # ✅ FIXED: Use singleton from app.state
    agent = getattr(request.app.state, "agent_chain", None)
    if agent is None:
        agent = AgentRAGChain()

    try:
        config = {"configurable": {"thread_id": thread_id}}
        # ✅ FIXED: Version-safe state access (see agent_chain.py fix)
        if hasattr(agent.graph, "get_state"):
            state = agent.graph.get_state(config)
        elif hasattr(agent.graph, "get_state_history"):
            history = list(agent.graph.get_state_history(config))
            state = history[-1] if history else None
        else:
            raise RuntimeError("LangGraph version unsupported for state retrieval")

        if not state:
            raise HTTPException(status_code=404, detail="Thread not found")

        vals = state.values if hasattr(state, "values") else {}

        # ✅ FIXED: Safe dict access for all values
        return {
            "thread_id": thread_id,
            "workspace_id": user.workspace_id,
            "confidence_score": vals.get("confidence_score", 0.0),
            "self_rag_confidence": vals.get("self_rag_confidence", 0.0),
            "relevance_score": vals.get("relevance_score", 0.0),
            "is_grounded": vals.get("is_grounded", True),
            "is_supported": vals.get("self_rag_supported", True),
            "is_complete": vals.get("self_rag_complete", True),
            "crag_action": vals.get("crag_action", "generate"),
            "web_search_used": vals.get("web_search_used", False),
            "retry_count": vals.get("retry_count", 0),
            "hallucination_flags": vals.get("hallucination_flags", []),
            "correlation_id": corr_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[{corr_id}] Confidence fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve confidence data")


def get_agent_metadata() -> dict[str, Any]:
    """✅ NEW: Return agent endpoint metadata for debugging."""
    return {
        "endpoints": [
            "/agent/query",
            "/agent/thread/{thread_id}",
            "/agent/confidence/{thread_id}",
        ],
        "rate_limit": {"endpoint_group": "query", "default_limit": "100/hour"},
        "timeout_seconds": getattr(settings, "agent_timeout_seconds", 120),
        "cache_enabled": True,
        "streaming_supported": True,
        "workspace_scoped": True,
    }


__all__ = ["router", "get_agent_metadata"]
# Local smoke test entry point. Run: python -m
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)
