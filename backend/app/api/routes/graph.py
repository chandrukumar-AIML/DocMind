
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from functools import partial
from typing import Annotated, Optional, Any, Final

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
    BackgroundTasks,
    Body,
)
from pydantic import BaseModel, Field

from app.config import (
    lazy_settings as settings,
)  # [OK] FIXED: lazy proxy avoids import-time crash
from app.core.ids import generate_correlation_id
from app.auth.dependencies import get_current_user, AuthenticatedUser
from app.models import ErrorResponse
from app.graph.graph_rag import GraphRAGRetriever, GraphRAGResult
from app.graph.extractor import GraphExtractor
from app.graph.neo4j_store import get_neo4j_store
from app.rag.chain import AdvancedRAGChain
from app.vectorstore.store_manager import VectorStoreManager
from app.core.llm_pool import get_llm  # ✅ FIXED: Use centralized LLM pool
from app.core.usage_tracker import log_action, ACTION_GRAPH_QUERY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])

_GRAPH_RETRIEVAL_TIMEOUT: Final = 60.0
_ANSWER_GENERATION_TIMEOUT: Final = 45.0
_SCHEMA_TIMEOUT: Final = 30.0


# ========================================================================
# PYDANTIC MODELS (DVMELTSS-V: Strict validation)
# ========================================================================
class GraphQueryRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    workspace_id: Optional[str] = Field(default=None, max_length=64)
    mode: str = Field(default="hybrid", pattern="^(vector|graph|hybrid)$")
    top_k: int = Field(default=5, ge=1, le=20)
    include_visualization: bool = Field(default=True)


class GraphNode(BaseModel):
    id: str
    name: str
    entity_type: str
    description: Optional[str] = None


class GraphEdge(BaseModel):
    from_id: str
    to_id: str
    relationship_type: str


class GraphVisualizationData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class GraphQueryResponse(BaseModel):
    answer: str
    retrieval_mode: str
    graph_context: Optional[str]
    vector_chunks: int
    graph_records: int
    visualization: Optional[GraphVisualizationData]
    latency_seconds: float
    citations: list[dict]
    correlation_id: str


class GraphSchemaResponse(BaseModel):
    nodes: dict[str, int]
    relationships: dict[str, int]
    workspace_id: str
    correlation_id: str


def _validate_graph_inputs(
    question: Optional[str],
    workspace_id: Optional[str],
    mode: Optional[str],
    entity_name: Optional[str],
    source_files: Optional[list],
    corr_id: str,
) -> tuple[bool, str]:
    """Validate graph endpoint inputs before processing."""
    if question is not None and (not isinstance(question, str) or not question.strip()):
        return False, "question must be a non-empty string"
    if workspace_id is not None and not isinstance(workspace_id, str):
        return False, "workspace_id must be a string or None"
    if mode is not None and mode not in ("vector", "graph", "hybrid"):
        return False, "mode must be one of: vector, graph, hybrid"
    if entity_name is not None and not isinstance(entity_name, str):
        return False, "entity_name must be a string or None"
    if source_files is not None and (
        not isinstance(source_files, list) or not all(isinstance(f, str) for f in source_files)
    ):
        return False, "source_files must be a list of strings or None"
    return True, ""


# ========================================================================
# INTERNAL: Graph helpers (DVMELTSS-B: Business logic separation)
# ========================================================================
async def _generate_answer_with_graph_context(
    question: str,
    graph_result: GraphRAGResult,
    rag_chain: AdvancedRAGChain,
    correlation_id: str,
) -> str:
    """Generate answer injecting graph context alongside vector context."""
    try:
        llm = get_llm(streaming=False, temperature_override=0.1)
    except Exception as e:
        logger.warning(f"[{correlation_id}] LLM unavailable for graph answer generation: {e}")
        return (
            "Graph context was retrieved, but answer generation is temporarily unavailable. "
            "Please verify the configured LLM provider and retry."
        )

    system_prompt = f"""You are DocuMind AI. Answer using ONLY the provided context.
Cite sources as [SOURCE: filename, page X].

Graph context:
{graph_result.graph_context[:2000] if hasattr(graph_result, 'graph_context') else ''}

Vector context:
{graph_result.answer_context[:2000] if hasattr(graph_result, 'answer_context') else ''}

If context is insufficient, say so clearly."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question},
    ]

    try:
        response = await asyncio.wait_for(
            llm.ainvoke(messages),
            timeout=_ANSWER_GENERATION_TIMEOUT,
        )
        return response.content if hasattr(response, "content") else str(response)
    except asyncio.TimeoutError:
        logger.warning(f"[{correlation_id}] Answer generation timed out after {_ANSWER_GENERATION_TIMEOUT}s")
        return "Answer generation timed out. Please try again."
    except Exception as e:
        logger.warning(f"[{correlation_id}] Answer generation failed: {e}")
        return (
            "Graph context was retrieved, but answer generation failed. "
            "Please verify the configured LLM provider and retry."
        )


def _build_visualization(graph_records: list[dict]) -> GraphVisualizationData:
    """Convert raw Neo4j records into vis.js-compatible node/edge format."""
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for rec in graph_records or []:
        if not isinstance(rec, dict):
            continue

        entity_name = rec.get("entity") or rec.get("name") or rec.get("start_name")
        entity_type = rec.get("type") or rec.get("start_type") or "Concept"
        description = rec.get("description", "")

        if entity_name and isinstance(entity_name, str) and entity_name not in nodes:
            node_id = str(uuid.uuid4())[:8]
            nodes[entity_name] = GraphNode(
                id=node_id,
                name=entity_name,
                entity_type=entity_type,
                description=description[:200] if description else None,
            )

        connections = rec.get("connections", [])
        if isinstance(connections, list):
            for conn in connections:
                if not isinstance(conn, dict):
                    continue
                target = conn.get("target")
                rel = conn.get("rel", "RELATED_TO")
                t_type = conn.get("target_type", "Concept")

                if target and isinstance(target, str) and target not in nodes:
                    nodes[target] = GraphNode(
                        id=str(uuid.uuid4())[:8],
                        name=target,
                        entity_type=t_type,
                    )

                if entity_name and target and entity_name in nodes and target in nodes:
                    edges.append(
                        GraphEdge(
                            from_id=nodes[entity_name].id,
                            to_id=nodes[target].id,
                            relationship_type=rel,
                        )
                    )

    return GraphVisualizationData(nodes=list(nodes.values()), edges=edges)


def _build_neighborhood_visualization(records: list[dict]) -> GraphVisualizationData:
    """Build visualization from neighborhood query results."""
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for rec in records or []:
        if not isinstance(rec, dict):
            continue

        start_name = rec.get("start_name", "")
        start_type = rec.get("start_type", "Concept")
        neigh_name = rec.get("neighbor_name", "")
        neigh_type = rec.get("neighbor_type", "Concept")
        rel_types = rec.get("rel_types", ["RELATED_TO"])

        for name, etype in [(start_name, start_type), (neigh_name, neigh_type)]:
            if name and isinstance(name, str) and name not in nodes:
                nodes[name] = GraphNode(
                    id=str(uuid.uuid4())[:8],
                    name=name,
                    entity_type=etype,
                )

        if (
            start_name
            and neigh_name
            and isinstance(start_name, str)
            and isinstance(neigh_name, str)
            and start_name in nodes
            and neigh_name in nodes
        ):
            rel = rel_types[0] if rel_types else "RELATED_TO"
            edges.append(
                GraphEdge(
                    from_id=nodes[start_name].id,
                    to_id=nodes[neigh_name].id,
                    relationship_type=rel,
                )
            )

    return GraphVisualizationData(nodes=list(nodes.values()), edges=edges)


# ========================================================================
# PUBLIC: FastAPI Endpoints
# ========================================================================
@router.post(
    "/query",
    response_model=GraphQueryResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid query"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        408: {"model": ErrorResponse, "description": "Timeout"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
    summary="Query documents using hybrid Graph + Vector RAG",
    description="Automatically selects retrieval mode (graph/vector/hybrid) based on query content.",
)
async def graph_query(
    request: GraphQueryRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    background_tasks: BackgroundTasks,
) -> GraphQueryResponse:
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="LLM service unavailable: OPENAI_API_KEY not configured",
        )

    corr_id = generate_correlation_id("graph_query")

    # ✅ Validate inputs
    is_valid, error = _validate_graph_inputs(request.question, request.workspace_id, request.mode, None, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    workspace_id = request.workspace_id or user.workspace_id

    logger.info(f"[{corr_id}] Graph query: user={user.user_id[:8]}... workspace={workspace_id} mode={request.mode}")

    start_ts = time.perf_counter()

    try:
        vector_store = VectorStoreManager(workspace_id=workspace_id)
        retriever = GraphRAGRetriever(store_manager=vector_store)

        if hasattr(retriever, "retrieve_async"):
            graph_result: GraphRAGResult = await asyncio.wait_for(
                retriever.retrieve_async(
                    query=request.question,
                    workspace_id=workspace_id,
                    mode=request.mode,
                    k_vector=request.top_k,
                ),
                timeout=_GRAPH_RETRIEVAL_TIMEOUT,
            )
        else:
            # Fallback: run in executor with timeout
            loop = asyncio.get_running_loop()  # FIXED: get_event_loop() deprecated in Python 3.10+
            graph_result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    partial(
                        retriever.retrieve,
                        query=request.question,
                        workspace_id=workspace_id,
                        mode=request.mode,
                        k_vector=request.top_k,
                    ),
                ),
                timeout=_GRAPH_RETRIEVAL_TIMEOUT,
            )

        rag_chain = AdvancedRAGChain()
        answer = await _generate_answer_with_graph_context(
            question=request.question,
            graph_result=graph_result,
            rag_chain=rag_chain,
            correlation_id=corr_id,
        )

        viz = None
        if request.include_visualization:
            viz = _build_visualization(getattr(graph_result, "graph_records", []))

        latency = time.perf_counter() - start_ts

        vector_docs = getattr(graph_result, "vector_docs", [])
        citations = []
        for doc in vector_docs[:3]:
            if doc is None:
                continue
            metadata = getattr(doc, "metadata", {}) or {}
            citations.append(
                {
                    "source_file": metadata.get("source_file", ""),
                    "page_number": (metadata.get("page_number", 0) or 0) + 1,
                    "chunk_text": getattr(doc, "page_content", "")[:200],
                    "relevance_score": metadata.get("relevance_score", 0.0),
                }
            )

        # ✅ Count this graph query against the workspace's daily plan quota.
        background_tasks.add_task(
            log_action,
            workspace_id=workspace_id,
            action_type=ACTION_GRAPH_QUERY,
            user_id=user.user_id,
        )

        return GraphQueryResponse(
            answer=answer,
            retrieval_mode=getattr(graph_result, "retrieval_mode", request.mode),
            graph_context=getattr(graph_result, "graph_context", ""),
            vector_chunks=len(vector_docs),
            graph_records=len(getattr(graph_result, "graph_records", [])),
            visualization=viz,
            latency_seconds=round(latency, 3),
            citations=citations,
            correlation_id=corr_id,
        )

    except asyncio.TimeoutError:
        latency = time.perf_counter() - start_ts
        logger.warning(f"[{corr_id}] Graph query timed out after {_GRAPH_RETRIEVAL_TIMEOUT}s")
        return GraphQueryResponse(
            answer=(
                "Graph retrieval timed out. Optional graph services may be busy or unavailable; "
                "please retry or reduce top_k."
            ),
            retrieval_mode=request.mode,
            graph_context="",
            vector_chunks=0,
            graph_records=0,
            visualization=None,
            latency_seconds=round(latency, 3),
            citations=[],
            correlation_id=corr_id,
        )
    except Exception as e:
        latency = time.perf_counter() - start_ts
        logger.warning(
            f"[{corr_id}] Graph query unavailable, returning degraded response: {e}",
            exc_info=True,
        )
        return GraphQueryResponse(
            answer=(
                "Graph query is temporarily unavailable. Optional graph, vector, or LLM services "
                "may not be configured in this environment."
            ),
            retrieval_mode=request.mode,
            graph_context="",
            vector_chunks=0,
            graph_records=0,
            visualization=None,
            latency_seconds=round(latency, 3),
            citations=[],
            correlation_id=corr_id,
        )


@router.get(
    "/schema",
    response_model=GraphSchemaResponse,
    summary="Get knowledge graph schema statistics",
)
async def get_graph_schema(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    workspace_id: Optional[str] = Query(default=None, max_length=64),
) -> GraphSchemaResponse:
    """Returns node and relationship counts for the current workspace."""
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="Graph service unavailable: OPENAI_API_KEY not configured",
        )

    corr_id = generate_correlation_id("graph_schema")

    ws_id = workspace_id or user.workspace_id

    try:
        schema = await asyncio.wait_for(
            asyncio.to_thread(lambda: get_neo4j_store().get_schema_summary(workspace_id=ws_id)),
            timeout=_SCHEMA_TIMEOUT,
        )
        return GraphSchemaResponse(
            nodes=schema.get("nodes", {}) if isinstance(schema, dict) else {},
            relationships=schema.get("relationships", {}) if isinstance(schema, dict) else {},
            workspace_id=ws_id,
            correlation_id=corr_id,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{corr_id}] Graph schema timed out — Neo4j may not be running")
        raise HTTPException(status_code=503, detail="Graph service unavailable (connection timeout)")
    except Exception as e:
        logger.warning(f"[{corr_id}] Graph schema unavailable: {e}")
        raise HTTPException(status_code=503, detail="Graph service unavailable")


@router.get(
    "/neighbors",
    summary="Get entity neighborhood for visualization",
)
async def get_entity_neighbors(
    entity_name: str = Query(..., min_length=1, max_length=255),
    hops: int = Query(default=2, ge=1, le=3),
    workspace_id: Optional[str] = Query(default=None, max_length=64),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
) -> dict:
    """Get N-hop neighborhood around a named entity for interactive graph exploration."""
    corr_id = generate_correlation_id("graph_neighbors")

    # ✅ Validate inputs
    is_valid, error = _validate_graph_inputs(None, workspace_id, None, entity_name, None, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    ws_id = workspace_id or (user.workspace_id if user else "default")

    try:
        store = get_neo4j_store()
        records = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: store.get_entity_neighborhood(
                    entity_name=entity_name,
                    hops=hops,
                    workspace_id=ws_id,
                )
            ),
            timeout=_SCHEMA_TIMEOUT,
        )
        viz = _build_neighborhood_visualization(records or [])

        return {
            "entity": entity_name,
            "hops": hops,
            "workspace_id": ws_id,
            "correlation_id": corr_id,
            "visualization": viz,
            "node_count": len(viz.nodes),
            "edge_count": len(viz.edges),
        }
    except asyncio.TimeoutError:
        logger.error(f"[{corr_id}] Graph neighbors timed out after {_SCHEMA_TIMEOUT}s")
        raise HTTPException(status_code=408, detail="Neighborhood retrieval timed out")
    except Exception as e:
        logger.error(f"[{corr_id}] Graph neighbors failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to retrieve neighborhood: {str(e)}")


@router.post(
    "/extract",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Extract entities/relationships from documents (async)",
)
async def extract_graph(
    source_files: list[str] = Body(..., min_length=1, max_length=10, description="List of source files to process"),
    workspace_id: Optional[str] = Query(default=None, max_length=64),
    user: Annotated[AuthenticatedUser, Depends(get_current_user)] = None,
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Queue entity/relationship extraction from documents (async job)."""
    corr_id = generate_correlation_id("graph_extract")

    # ✅ Validate inputs
    is_valid, error = _validate_graph_inputs(None, workspace_id, None, None, source_files, corr_id)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    ws_id = workspace_id or (user.workspace_id if user else "default")

    task_id = str(uuid.uuid4())

    if background_tasks:
        def _do_extract():
            try:
                extractor = GraphExtractor()
                store = get_neo4j_store()

                for source_file in source_files[:10]:  # Cap at 10 files
                    vector_store = VectorStoreManager(workspace_id=ws_id)
                    chunks = asyncio.run(vector_store.get_document_chunks_async(source_file))

                    results = extractor.extract_from_documents(chunks, source_file, ws_id)
                    for result in results:
                        for entity in getattr(result, "entities", []):
                            if entity and hasattr(entity, "id"):
                                store.upsert_entity(
                                    entity_id=entity.id,
                                    entity_type=getattr(entity, "entity_type", ""),
                                    name=getattr(entity, "name", ""),
                                    properties={"description": getattr(entity, "description", "")},
                                    workspace_id=ws_id,
                                )
                        for rel in getattr(result, "relationships", []):
                            if rel and hasattr(rel, "from_entity_id"):
                                store.upsert_relationship(
                                    from_entity_id=rel.from_entity_id,
                                    to_entity_id=rel.to_entity_id,
                                    relationship_type=getattr(rel, "relationship_type", ""),
                                    properties=getattr(rel, "properties", {}),
                                    workspace_id=ws_id,
                                )

                logger.info(f"[{corr_id}] Graph extraction complete: task={task_id}")
            except Exception as e:
                logger.error(f"[{corr_id}] Graph extraction failed: {e}", exc_info=True)

        background_tasks.add_task(_do_extract)

    return {
        "task_id": task_id,
        "status": "queued",
        "source_files": source_files,
        "workspace_id": ws_id,
        "correlation_id": corr_id,
        "message": "Graph extraction queued. Check status via /tasks/{task_id}",
    }


def get_graph_metadata() -> dict[str, Any]:
    """✅ NEW: Return graph API metadata for monitoring."""
    return {
        "endpoints": [
            "/graph/query",
            "/graph/schema",
            "/graph/neighbors",
            "/graph/extract",
        ],
        "timeouts": {
            "graph_retrieval_seconds": _GRAPH_RETRIEVAL_TIMEOUT,
            "answer_generation_seconds": _ANSWER_GENERATION_TIMEOUT,
            "schema_timeout_seconds": _SCHEMA_TIMEOUT,
        },
        "supported_modes": ["vector", "graph", "hybrid"],
        "max_top_k": 20,
        "max_extract_files": 10,
        "workspace_scoped": True,
        "neo4j_integration": True,
    }


__all__ = ["router", "get_graph_metadata"]
# Local smoke test entry point. Run: python -m

