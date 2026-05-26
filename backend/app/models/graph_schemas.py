# backend/app/models/graph_schemas.py
# DVMELTSS-FIX: V - Validate, M - Modular, S - Security

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

# ✅ FIXED: Import field_validator from pydantic
from pydantic import BaseModel, Field, ConfigDict, field_validator

# DVMELTSS-M: Import centralized utilities
from app.core.schema_utils import CorrelationIdField, sanitize_text


class GraphRetrievalMode(str, Enum):
    """Supported graph retrieval strategies."""
    NODES_ONLY = "nodes_only"
    EDGES_ONLY = "edges_only"
    SUBGRAPH = "subgraph"
    PATH = "path"
    FULL = "full"


class GraphQueryRequest(BaseModel):
    """Request for graph-based retrieval."""
    query: str = Field(..., min_length=3, max_length=2000, description="Natural language query")
    mode: GraphRetrievalMode = Field(default=GraphRetrievalMode.SUBGRAPH)
    max_nodes: int = Field(default=50, ge=1, le=200, description="Maximum nodes to retrieve")
    max_hops: int = Field(default=2, ge=1, le=5, description="Maximum relationship hops")
    filter_entity_type: Optional[str] = Field(default=None, description="Filter to specific entity type")
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing

    @field_validator("query")  # ✅ Now field_validator is imported
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        # FIXED: Use centralized sanitizer
        return sanitize_text(v, max_length=2000, min_length=3)


class GraphNode(BaseModel):
    """Graph node representation for API responses."""
    id: str
    label: str
    entity_type: str
    properties: dict[str, Any]
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class GraphEdge(BaseModel):
    """Graph edge representation for API responses."""
    source_id: str
    target_id: str
    relationship_type: str
    properties: dict[str, Any]
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class GraphVisualizationData(BaseModel):
    """Graph data formatted for frontend visualization."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    layout_hint: Optional[str] = Field(default=None, description="Suggested layout: force, hierarchical, circular")
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class GraphQueryResponse(BaseModel):
    """Response for graph query with results."""
    query: str
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    summary: str
    node_count: int
    edge_count: int
    latency_seconds: float
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


class GraphSchemaResponse(BaseModel):
    """Response describing available graph schema."""
    entity_types: List[str]
    relationship_types: List[str]
    sample_queries: List[str]
    correlation_id: Optional[str] = CorrelationIdField  # FIXED: Added for tracing


# DVMELTSS-M: Explicit module exports
__all__ = [
    "GraphRetrievalMode", "GraphQueryRequest", "GraphNode",
    "GraphEdge", "GraphVisualizationData", "GraphQueryResponse", "GraphSchemaResponse",
]
# Local smoke test entry point. Run: python -m 
if __name__ == "__main__":
    import sys
    from app.core.module_smoke import run_module_smoke

    run_module_smoke(sys.modules[__name__], __file__)

